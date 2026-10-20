"""Local scene browser and replacement editor; all rendering stays off Tk's thread."""
from __future__ import annotations

import os
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import uuid
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageTk


COLORS = {
    'bg': '#1D1A17', 'card': '#28231F', 'field': '#342E28',
    'border': '#4B4035', 'text': '#F2EADF', 'muted': '#BCAF9C',
    'accent': '#E8B46B', 'ink': '#241B11', 'selection': '#795632',
}


def _selection_paths(value):
    """Keep an explicitly selected finished movie as the exact audio source."""
    selected = Path(value).expanduser().absolute()
    explicit_movie = selected.suffix.lower() == '.mp4'
    variant_project = (selected.name.startswith('video_zene_')
                       and selected.name.endswith('.projekt.json'))
    if explicit_movie or variant_project:
        if not selected.is_file():
            raise ValueError('A kiválasztott videóváltozat vagy projektfájl már nem található.')
    project = selected.parent if selected.is_file() else selected
    scene_file = project.name == 'jelenetek' and (project.parent / 'jelenetek.json').is_file()
    if scene_file:
        project = project.parent
    if explicit_movie and not scene_file:
        soundtrack = selected
    elif variant_project:
        soundtrack = selected.with_name(selected.name[:-len('.projekt.json')] + '.mp4')
        if not soundtrack.is_file():
            raise ValueError('Ehhez a kiválasztott zenés változathoz nem található a hozzá tartozó MP4. '
                             'Válaszd ki a megőrizni kívánt videót közvetlenül.')
    else:
        soundtrack = project / 'video.mp4'
    return project, soundtrack


def show(parent, project_folder=None, *, on_done=None, on_busy=None, hidden=False):
    """Open an editor. Callbacks always run on the Tk main thread.

    ``on_busy(engine)`` lets the main window share its cancellation action.
    Credentials, remote services and AI generation are not used here.
    """
    return SceneLibraryWindow(parent, project_folder, on_done, on_busy, hidden=hidden).window


class SceneLibraryWindow:
    def __init__(self, parent, project_folder, on_done, on_busy, *, hidden=False):
        self.parent = parent
        self.on_done = on_done
        self.on_busy = on_busy
        self.library = None
        self.soundtrack = None
        self.replacements = {}
        self.result = None
        self._events = queue.Queue()
        self._task = None
        self._preview_task = None
        self._preview_serial = 0
        self._photo = None
        self._closing = False
        self._destroyed = False
        self._notified_busy = False
        self._worker_error = ''

        window = self.window = tk.Toplevel(parent)
        if hidden:
            window.attributes('-alpha', 0)
        else:
            window.transient(parent.winfo_toplevel())
            # Keep one processing workflow in control. This is an editor
            # window, without an extra confirmation dialog or autoplay.
            window.bind('<Map>', self._claim_focus, add='+')
        window.scene_library_view = self
        window.title('Jelenetek és javítás • YouTube Műhely')
        window.configure(background=COLORS['bg'])
        window.geometry('980x680')
        window.minsize(760, 570)
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.bind('<Destroy>', self._external_destroy, add='+')
        self._styles()
        self._build()
        window.after(70, self._poll)
        if project_folder:
            self.open(project_folder)

    def _styles(self):
        style = ttk.Style(self.window)
        c = COLORS
        # Existing parent buttons keep the app's theme. Treeview needs its own
        # selection map so Windows' usual blue highlight does not appear.
        style.configure('Scenes.Treeview', background=c['field'],
                        fieldbackground=c['field'], foreground=c['text'],
                        rowheight=34, bordercolor=c['border'], font=('Segoe UI', 10))
        style.map('Scenes.Treeview', background=[('selected', c['selection'])],
                  foreground=[('selected', c['text'])])
        style.configure('Scenes.Treeview.Heading', background=c['card'],
                        foreground=c['muted'], bordercolor=c['border'],
                        font=('Segoe UI', 9, 'bold'))
        style.map('Scenes.Treeview.Heading', background=[('active', c['field'])])

    def _claim_focus(self, event):
        if event.widget is self.window and not self._destroyed:
            self.window.grab_set()

    def _label(self, parent, text='', *, muted=False, **kwargs):
        return tk.Label(parent, text=text, background=COLORS['bg'],
                        foreground=COLORS['muted' if muted else 'text'],
                        font=kwargs.pop('font', ('Segoe UI', 9 if muted else 10)), justify='left', **kwargs)

    def _button(self, parent, text, command, **kwargs):
        return ttk.Button(parent, text=text, command=command, **kwargs)

    def _build(self):
        w = self.window
        outer = tk.Frame(w, background=COLORS['bg'])
        outer.pack(fill='both', expand=True, padx=18, pady=15)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        self._label(outer, 'Jelenetek és javítás',
                    font=('Segoe UI', 16, 'bold')).grid(row=0, column=0, sticky='w')
        self._label(outer, 'Cseréld a kiválasztott jelenet képét egy saját videóra. '
                    'Az eredeti narráció, zene és jelenethossz megmarad.', muted=True,
                    wraplength=890).grid(row=1, column=0, sticky='ew', pady=(5, 10))

        source = tk.Frame(outer, background=COLORS['bg'])
        source.grid(row=2, column=0, sticky='ew', pady=(0, 10))
        source.columnconfigure(2, weight=1)
        self.open_button = self._button(source, 'Projekt megnyitása…', self.choose_project)
        self.open_button.grid(row=0, column=0, padx=(0, 7))
        self.folder_button = self._button(source, 'Mappa kiválasztása…', self.choose_folder)
        self.folder_button.grid(row=0, column=1, padx=(0, 10))
        self.project_label = self._label(source, 'Még nincs megnyitott projekt.', muted=True,
                                         wraplength=390)
        self.project_label.grid(row=0, column=2, sticky='ew')

        content = tk.Frame(outer, background=COLORS['bg'])
        content.grid(row=3, column=0, sticky='nsew')
        content.columnconfigure(0, weight=4, minsize=280)
        content.columnconfigure(1, weight=5, minsize=320)
        content.rowconfigure(0, weight=1)
        left = tk.Frame(content, background=COLORS['bg'])
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 14))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, columns=('length', 'state'), show='tree headings',
                                  style='Scenes.Treeview', selectmode='browse')
        self.tree.heading('#0', text='Jelenet')
        self.tree.heading('length', text='Hossz')
        self.tree.heading('state', text='Változtatás')
        self.tree.column('#0', width=125, minwidth=90, stretch=True)
        self.tree.column('length', width=58, minwidth=52, stretch=False, anchor='e')
        self.tree.column('state', width=88, minwidth=80, stretch=False)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self._selected)
        self.count_label = self._label(left, '0 jelenet', muted=True)
        self.count_label.grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))

        right = tk.Frame(content, background=COLORS['bg'])
        right.grid(row=0, column=1, sticky='nsew')
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self.preview = tk.Label(right, text='Válassz egy jelenetet az előnézethez.',
                                background=COLORS['field'], foreground=COLORS['muted'],
                                anchor='center', font=('Segoe UI', 10),
                                highlightthickness=1, highlightbackground=COLORS['border'])
        self.preview.grid(row=0, column=0, sticky='nsew')
        self.details = self._label(right, '', muted=True, wraplength=460)
        self.details.grid(row=1, column=0, sticky='ew', pady=(7, 0))
        self.replace_label = self._label(right, 'Nincs kijelölt jelenet.', muted=True,
                                         wraplength=460)
        self.replace_label.grid(row=2, column=0, sticky='ew', pady=(6, 6))
        scene_actions = tk.Frame(right, background=COLORS['bg'])
        scene_actions.grid(row=3, column=0, sticky='ew')
        scene_actions.columnconfigure((0, 1), weight=1)
        self.play_button = self._button(scene_actions, 'Jelenet lejátszása', self.play)
        self.play_button.grid(row=0, column=0, sticky='ew', padx=(0, 6), pady=(0, 6))
        self.replace_button = self._button(scene_actions, 'Cserevideó kiválasztása…', self.choose_replacement)
        self.replace_button.grid(row=0, column=1, sticky='ew', pady=(0, 6))
        self.clear_button = self._button(scene_actions, 'Eredeti kép visszaállítása', self.clear_replacement)
        self.clear_button.grid(row=1, column=0, columnspan=2, sticky='ew')

        footer = tk.Frame(outer, background=COLORS['bg'])
        footer.grid(row=4, column=0, sticky='ew', pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        self.notice = self._label(footer, 'Csak a mozgókép változik. A cserevideó saját hangját '
                                  'nem használjuk. Új fájl készül; az eredeti megmarad.',
                                  muted=True, wraplength=890)
        self.notice.grid(row=0, column=0, sticky='ew')
        self.status = self._label(footer, 'Nyiss meg egy korábban elkészített projektet.',
                                  wraplength=890)
        self.status.grid(row=1, column=0, sticky='ew', pady=(7, 5))
        self.progress = ttk.Progressbar(footer, mode='determinate', maximum=100,
                                       style='Warm.Horizontal.TProgressbar')
        self.progress.grid(row=2, column=0, sticky='ew', pady=(0, 9))
        actions = tk.Frame(footer, background=COLORS['bg'])
        actions.grid(row=3, column=0, sticky='ew')
        actions.columnconfigure(0, weight=1)
        self.export_button = self._button(actions, 'Új változat mentése', self.export,
                                           style='Accent.TButton')
        self.export_button.grid(row=0, column=0, sticky='w')
        self.cancel_button = self._button(actions, 'Megszakítás', self.cancel)
        self.cancel_button.grid(row=0, column=1, padx=(7, 0))
        self.result_button = self._button(actions, 'Mentés helye', self.open_result)
        self.result_button.grid(row=0, column=2, padx=(7, 0))
        self.close_button = self._button(actions, 'Bezárás', self.close)
        self.close_button.grid(row=0, column=3, padx=(7, 0))
        w.bind('<Configure>', self._resize, add='+')
        self._refresh_controls()

    def _resize(self, event):
        if event.widget is not self.window:
            return
        available = max(650, event.width - 45)
        for widget in (self.notice, self.status):
            widget.configure(wraplength=available)
        detail_width = max(280, int(available * .52))
        self.details.configure(wraplength=detail_width)
        self.replace_label.configure(wraplength=detail_width)

    def choose_project(self):
        if self._task or self._closing:
            return
        selected = filedialog.askopenfilename(parent=self.window, title='Elkészült projekt megnyitása',
                    filetypes=[('Projekt vagy jelenetlista', '*.json'), ('Elkészült videó', '*.mp4'),
                               ('Minden fájl', '*.*')])
        if selected:
            self.open(selected)

    def choose_folder(self):
        if self._task or self._closing:
            return
        selected = filedialog.askdirectory(parent=self.window, title='Projekt mappájának kiválasztása')
        if selected:
            self.open(selected)

    def _new_engine(self):
        from ytmuhely import Engine
        return Engine(lambda value: self._events.put(('log', str(value))),
                      lambda value: self._events.put(('progress', value)))

    def open(self, folder):
        if self._task or self._closing:
            return
        self._stop_preview()
        self.library = None
        self.soundtrack = None
        self.replacements.clear()
        self.result = None
        self.tree.delete(*self.tree.get_children())
        self._photo = None
        self.preview.configure(image='', text='Jelenetek betöltése…')
        self.project_label.configure(text=Path(folder).name)
        self.status.configure(text='A mentett jeleneteket olvassuk…')
        self._worker_error = ''
        engine = self._new_engine()
        def worker():
            try:
                from scene_library import load
                # The backend deliberately accepts only a project directory;
                # the browser additionally lets people choose its movie/JSON.
                project, soundtrack = _selection_paths(folder)
                library = load(project, engine=engine)
                self._events.put(('loaded', (library, soundtrack)))
            except Exception as error:
                self._events.put(('error', str(error)))
            finally:
                self._events.put(('finished', 'load'))
        self._task = ('load', engine, threading.Thread(target=worker, name='Jelenetek betöltése'))
        self._task[2].start()
        self._refresh_controls()

    def _loaded(self, library, soundtrack):
        self.library = library
        self.soundtrack = Path(soundtrack)
        self.project_label.configure(text=Path(library.root).name + '\nHang és zene: ' + self.soundtrack.name)
        for scene in library.scenes:
            self.tree.insert('', 'end', iid=str(scene.index), text=f'{scene.index + 1}. jelenet',
                             values=(f'{float(scene.duration):g} mp', 'Eredeti'))
        total = getattr(library, 'expected_scenes', len(library.scenes))
        self.count_label.configure(text=f'{len(library.scenes)} / {total} jelenet')
        if library.status == 'complete':
            self.status.configure(text='Válassz egy jelenetet. Megőrzött hang és zene: '
                                  + self.soundtrack.name + '. Az eredeti videó megmarad.')
        else:
            self.status.configure(text='Ez a projekt még nem készült el teljesen. A mentett jelenetek '
                                  'megnézhetők; új változatot az eredeti videó elkészülte után menthetsz.')
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
        else:
            self.preview.configure(text='Még nincs elkészült jelenet ebben a projektben.')
        self._refresh_controls()

    def _scene(self):
        if not self.library:
            return None
        selected = self.tree.selection()
        if not selected:
            return None
        index = int(selected[0])
        return next((scene for scene in self.library.scenes if scene.index == index), None)

    def _selected(self, event=None):
        scene = self._scene()
        if scene is None:
            self._refresh_controls()
            return
        size = getattr(scene, 'size', None)
        dimensions = f' • {size[0]} × {size[1]}' if size and len(size) == 2 else ''
        self.details.configure(text=f'{scene.index + 1}. jelenet • {float(scene.duration):g} másodperc'
                               + dimensions + '\n' + str(getattr(scene, 'label', '') or '')[:240])
        replacement = self.replacements.get(scene.index)
        self.replace_label.configure(text=('Csere: ' + Path(replacement).name) if replacement
                                      else 'Eredeti jelenet • nincs kijelölt csere')
        self._load_preview(Path(replacement) if replacement else Path(scene.video), scene.duration)
        self._refresh_controls()

    def _stop_preview(self):
        self._preview_serial += 1
        if self._preview_task:
            self._preview_task[0].cancel.set()

    def _load_preview(self, source, duration):
        self._stop_preview()
        serial = self._preview_serial
        engine = self._new_engine()
        self._photo = None
        self.preview.configure(image='', text='Előnézet készül…')
        def worker():
            try:
                if not source.is_file():
                    raise ValueError('A jelenet videófájlja nem található.')
                from ytmuhely import ffmpeg
                # Plain mkdir avoids TemporaryDirectory's restrictive Windows
                # ACL behaviour in the bundled runtime. Cleanup only touches
                # this one owned file and its uniquely named empty directory.
                folder = Path(tempfile.gettempdir()) / ('ytmuhely_elonezet_' + uuid.uuid4().hex)
                folder.mkdir()
                target = folder / 'kep.png'
                try:
                    # The protocol allowlist also prevents a locally selected
                    # playlist from fetching remote content during preview.
                    engine.run([ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y',
                                '-nostdin', '-protocol_whitelist', 'file,pipe', '-hwaccel', 'none',
                                '-ss', str(min(2.0, max(0.0, float(duration) * .2))),
                                '-i', str(source.resolve()), '-map', '0:V:0', '-an', '-sn', '-dn',
                                '-frames:v', '1', '-vf', 'scale=640:400:force_original_aspect_ratio=decrease',
                                '-threads', '1', str(target)], timeout=25)
                    with Image.open(target) as picture:
                        picture.load()
                        value = picture.convert('RGB')
                finally:
                    try:
                        target.unlink(missing_ok=True)
                        folder.rmdir()
                    except OSError:
                        pass
                engine.check()
                self._events.put(('preview', (serial, value)))
            except Exception:
                if not engine.cancel.is_set():
                    self._events.put(('preview_error', serial))
            finally:
                self._events.put(('preview_finished', serial))
        thread = threading.Thread(target=worker, name='Jelenet előnézete')
        # Keep every preview worker until it exits: changing selection rapidly
        # must not leave an older FFmpeg child outside the close/cancel path.
        if not hasattr(self, '_preview_workers'):
            self._preview_workers = []
        self._preview_workers.append((engine, thread, serial))
        self._preview_task = (engine, thread, serial)
        thread.start()

    def choose_replacement(self):
        scene = self._scene()
        if self._task or self._closing or scene is None:
            return
        source = filedialog.askopenfilename(parent=self.window, title='Mozgókép a kiválasztott jelenethez',
            filetypes=[('Videók', '*.mp4 *.mov *.mkv *.webm *.avi *.m4v'), ('Minden fájl', '*.*')])
        if source:
            self.replacements[scene.index] = str(Path(source).resolve())
            self.tree.set(str(scene.index), 'state', 'Csere vár')
            self._selected()
            self.status.configure(text=f'{len(self.replacements)} jelenet képe változik. '
                                  'A videókat mentés előtt ellenőrizzük. Narráció és zene változatlan.')

    def clear_replacement(self):
        scene = self._scene()
        if self._task or scene is None:
            return
        self.replacements.pop(scene.index, None)
        self.tree.set(str(scene.index), 'state', 'Eredeti')
        self._selected()
        self.status.configure(text=f'{len(self.replacements)} kijelölt csere. '
                              'Az eredeti fájlokat nem módosítottuk.')

    def play(self):
        scene = self._scene()
        if self._task or scene is None:
            return
        self._open_local(self.replacements.get(scene.index, str(scene.video)))

    def _open_local(self, value):
        try:
            local = Path(value).resolve(strict=True)
            os.startfile(str(local))
        except (OSError, AttributeError, ValueError) as error:
            self.status.configure(text='A fájl nem nyitható meg: ' + str(error))

    def export(self):
        if self._task or self._closing or not self.library or self.library.status != 'complete' or not self.replacements:
            return
        self._stop_preview()
        engine = self._new_engine()
        replacements = dict(self.replacements)
        folder = self.library.root
        soundtrack = self.soundtrack
        self._worker_error = ''
        self.result = None
        self.progress['value'] = 0
        self.status.configure(text='Új változat készül. Az eredeti hangot és időzítést megőrizzük…')
        def worker():
            try:
                from scene_library import export_variant
                result = export_variant(engine, folder, replacements, soundtrack=soundtrack)
                self._events.put(('exported', Path(result)))
            except Exception as error:
                self._events.put(('error', str(error)))
            finally:
                self._events.put(('finished', 'export'))
        self._task = ('export', engine, threading.Thread(target=worker, name='Új videóváltozat'))
        try:
            if self.on_busy:
                self.on_busy(engine)
                self._notified_busy = True
        except Exception as error:
            self._task = None
            self.status.configure(text='A mentés nem indult el: ' + str(error))
            self._refresh_controls()
            return
        self._task[2].start()
        self._refresh_controls()

    def cancel(self):
        if self._task:
            self._task[1].cancel.set()
            self.status.configure(text='Megszakítás folyamatban… Az eredeti fájlok megmaradnak.')
            self.cancel_button.state(['disabled'])

    def open_result(self):
        if self.result:
            self._open_local(self.result.parent)

    def _refresh_controls(self):
        busy = bool(self._task) or self._closing
        scene = self._scene()
        for widget in (self.open_button, self.folder_button):
            widget.state(['disabled'] if busy else ['!disabled'])
        for widget in (self.play_button, self.replace_button):
            widget.state(['disabled'] if busy or scene is None else ['!disabled'])
        self.clear_button.state(['disabled'] if busy or scene is None or scene.index not in self.replacements
                                else ['!disabled'])
        ready = self.library is not None and self.library.status == 'complete'
        self.export_button.state(['disabled'] if busy or not ready or not self.replacements else ['!disabled'])
        self.cancel_button.state(['!disabled'] if self._task and not self._task[1].cancel.is_set() else ['disabled'])
        self.result_button.state(['!disabled'] if self.result and not busy else ['disabled'])
        self.close_button.state(['disabled'] if self._closing else ['!disabled'])
        # Selecting another scene during an export would trigger additional
        # decoding. Disable mouse and keyboard selection until export exits.
        self.tree.configure(selectmode='none' if busy else 'browse')

    def _poll(self):
        if self._destroyed:
            return
        try:
            while True:
                kind, value = self._events.get_nowait()
                if kind == 'loaded' and not self._closing:
                    self._loaded(*value)
                elif kind == 'error':
                    self._worker_error = value
                    self.status.configure(text='A művelet nem fejeződött be. ' + value +
                                          ' Az eredeti videó változatlan.')
                elif kind == 'log' and not self._worker_error and not self._closing:
                    self.status.configure(text=value)
                elif kind == 'progress':
                    self.progress['value'] = max(0, min(100, float(value)))
                elif kind == 'preview' and not self._closing:
                    serial, picture = value
                    if serial == self._preview_serial:
                        width = max(140, self.preview.winfo_width() - 14)
                        height = max(100, self.preview.winfo_height() - 14)
                        picture.thumbnail((width, height), Image.Resampling.LANCZOS)
                        self._photo = ImageTk.PhotoImage(picture, master=self.window)
                        self.preview.configure(image=self._photo, text='')
                elif kind == 'preview_error' and value == self._preview_serial:
                    self.preview.configure(image='', text='Ehhez a videóhoz nem sikerült előnézetet készíteni.\n'
                                           'A Lejátszás gombbal megnyithatod.')
                elif kind == 'exported':
                    self.result = value
                    self.progress['value'] = 100
                    self.status.configure(text='Elkészült az új változat: ' + value.name +
                                          ' Az eredeti videó megmaradt.')
                    if self.on_done:
                        try:
                            self.on_done(value)
                        except Exception as error:
                            self.status.configure(text='A videó elkészült. A főablak frissítése nem sikerült: ' + str(error))
                elif kind == 'finished':
                    if self._task and self._task[0] == value:
                        self._task = None
                        if value == 'export' and self._notified_busy:
                            self._notified_busy = False
                            if self.on_busy:
                                try:
                                    self.on_busy(None)
                                except Exception:
                                    pass
                        self._refresh_controls()
        except queue.Empty:
            pass
        self._preview_workers = [item for item in getattr(self, '_preview_workers', ()) if item[1].is_alive()]
        if self._closing and not self._task and not self._preview_workers:
            self.window.destroy()
            return
        self.window.after(70, self._poll)

    def close(self):
        if self._destroyed or self._closing:
            return
        self._closing = True
        self.cancel()
        self._stop_preview()
        for engine, thread, serial in getattr(self, '_preview_workers', ()):
            engine.cancel.set()
        self.status.configure(text='Bezárás folyamatban… A futó helyi műveletek leállnak.')
        self._refresh_controls()

    def _external_destroy(self, event):
        if event.widget is not self.window:
            return
        self._destroyed = True
        if self._task:
            self._task[1].cancel.set()
        for engine, thread, serial in getattr(self, '_preview_workers', ()):
            engine.cancel.set()
        if self._notified_busy and self.on_busy:
            self._notified_busy = False
            try:
                self.on_busy(None)
            except Exception:
                pass

    def diagnostics(self):
        """Small non-secret snapshot for actual hidden-Tk release checks."""
        def widget_info(widget):
            return {'mapped': bool(widget.winfo_ismapped()),
                    'bounds': [widget.winfo_rootx() - self.window.winfo_rootx(),
                               widget.winfo_rooty() - self.window.winfo_rooty(),
                               widget.winfo_width(), widget.winfo_height()],
                    'enabled': not widget.instate(['disabled'])}
        return {'window': [self.window.winfo_width(), self.window.winfo_height()],
                'palette': dict(COLORS), 'scene_count': len(self.tree.get_children()),
                'library_status': getattr(self.library, 'status', None),
                'soundtrack_source': self.soundtrack.name if self.soundtrack else None,
                'thumbnail_ready': self._photo is not None,
                'selected': list(self.tree.selection()), 'pending_changes': len(self.replacements),
                'busy': self._task is not None, 'closing': self._closing,
                'export': widget_info(self.export_button), 'cancel': widget_info(self.cancel_button),
                'replace': widget_info(self.replace_button), 'play': widget_info(self.play_button),
                'open': widget_info(self.open_button), 'close': widget_info(self.close_button),
                'status': self.status.cget('text')}


def diagnose(project_folder, report_path):
    """Run a real, invisible 900×620 Tk/CPU-thumbnail release check.

    This deliberately uses the same asynchronous load and preview paths as
    the visible editor. It never plays media, exports a video or opens a URL.
    Returns the report and writes it to ``report_path`` after workers stop.
    """
    root = tk.Tk()
    root.withdraw()
    root.attributes('-alpha', 0)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', background=COLORS['card'], foreground=COLORS['text'], font=('Segoe UI', 10))
    style.configure('TButton', background='#44392E', foreground=COLORS['text'], padding=(10, 7))
    style.map('TButton', background=[('active', '#584733')])
    style.configure('Accent.TButton', background=COLORS['accent'], foreground=COLORS['ink'], padding=(12, 8))
    style.configure('Warm.Horizontal.TProgressbar', background=COLORS['accent'], troughcolor=COLORS['field'])
    window = show(root, project_folder, hidden=True)
    window.geometry('900x620')
    view = window.scene_library_view
    started = time.monotonic()
    report = {}
    def inspect():
        nonlocal report
        workers = getattr(view, '_preview_workers', ())
        idle = not view._task and not any(item[1].is_alive() for item in workers)
        elapsed = time.monotonic() - started
        if elapsed < 40 and not (idle and elapsed > .5):
            root.after(100, inspect)
            return
        report = view.diagnostics()
        report['real_hidden_tk'] = True
        report['network_or_paid_requests'] = False
        report['cpu_thumbnail'] = True
        report['timed_out'] = not idle
        report['error'] = view._worker_error
        visible = all(item['mapped'] and item['bounds'][0] >= 0 and item['bounds'][1] >= 0
                      and item['bounds'][0] + item['bounds'][2] <= 900
                      and item['bounds'][1] + item['bounds'][3] <= 620
                      for key, item in report.items()
                      if key in ('export', 'cancel', 'replace', 'play', 'open', 'close'))
        report['actions_visible'] = visible
        report['status'] = 'PASS' if (idle and not report['error'] and visible
            and report['window'] == [900, 620] and report['scene_count'] > 0
            and report['thumbnail_ready']) else 'FAIL'
        view.close()
        root.after(100, finish)
    def finish():
        if window.winfo_exists():
            root.after(100, finish)
            return
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        root.destroy()
    root.after(100, inspect)
    root.mainloop()
    return report
