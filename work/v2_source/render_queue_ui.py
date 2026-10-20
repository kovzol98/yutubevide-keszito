"""Hungarian local render queue window; Tk is only accessed on its own thread."""
from __future__ import annotations

from collections import Counter
import copy
import json
import os
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, ttk

from scene_library_ui import COLORS


STATUS_NAMES = {
    'pending': 'Várakozik', 'running': 'Készül', 'complete': 'Elkészült',
    'failed': 'Hiba', 'cancelled': 'Megszakítva', 'interrupted': 'Félbeszakadt',
}
RETRY_STATUSES = frozenset(('failed', 'cancelled', 'interrupted'))


def show(parent, store, runner, *, get_project=None, on_start=None,
         on_start_failed=None, hidden=False):
    """Show a queue owned by the application, without owning its worker lifetime.

    ``get_project`` snapshots the current form on the Tk thread. ``on_start``
    must return true before the queue may render, allowing the main application
    to prevent concurrent GPU work. Closing this window leaves the queue running.
    """
    return QueueWindow(parent, store, runner, get_project=get_project,
                       on_start=on_start, on_start_failed=on_start_failed, hidden=hidden).window


class QueueWindow:
    def __init__(self, parent, store, runner, *, get_project=None, on_start=None,
                 on_start_failed=None, hidden=False):
        self.store = store
        self.runner = runner
        self.get_project = get_project
        self.on_start = on_start
        self.on_start_failed = on_start_failed
        self._destroyed = False
        self._after = None
        self._jobs = []
        self._signature = None
        self._detail_signature = None
        self._notice = ''
        self._notice_until = 0.0
        self._stop_requested = False
        self._was_running = False

        window = self.window = tk.Toplevel(parent)
        if hidden:
            window.attributes('-alpha', 0)
        else:
            window.transient(parent.winfo_toplevel())
        window.queue_view = self
        window.title('Munkasor • YouTube Műhely')
        window.configure(background=COLORS['bg'])
        window.geometry('980x660')
        window.minsize(760, 560)
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.bind('<Destroy>', self._external_destroy, add='+')
        self._styles()
        self._build()
        self.refresh()
        self._after = window.after(200, self._poll)

    def _styles(self):
        c = COLORS
        style = ttk.Style(self.window)
        style.configure('Queue.Treeview', background=c['field'],
                        fieldbackground=c['field'], foreground=c['text'],
                        rowheight=32, bordercolor=c['border'], font=('Segoe UI', 10))
        style.map('Queue.Treeview', background=[('selected', c['selection'])],
                  foreground=[('selected', c['text'])])
        style.configure('Queue.Treeview.Heading', background=c['card'],
                        foreground=c['muted'], bordercolor=c['border'],
                        font=('Segoe UI', 9, 'bold'))
        style.map('Queue.Treeview.Heading', background=[('active', c['field'])])
        style.configure('Queue.TButton', background='#44392E', foreground=c['text'],
                        padding=(10, 6), font=('Segoe UI', 10))
        style.map('Queue.TButton', background=[('active', '#584733')],
                  foreground=[('disabled', '#8A7D6E')])
        style.configure('Queue.Accent.TButton', background=c['accent'],
                        foreground=c['ink'], padding=(12, 7), font=('Segoe UI', 10, 'bold'))
        style.map('Queue.Accent.TButton', background=[('active', '#F1C68B')],
                  foreground=[('disabled', '#8A7D6E')])
        style.configure('Queue.Horizontal.TProgressbar', background=c['accent'],
                        troughcolor=c['field'])

    @staticmethod
    def _label(parent, text='', *, muted=False, **kwargs):
        return tk.Label(parent, text=text, background=COLORS['bg'],
                        foreground=COLORS['muted' if muted else 'text'],
                        font=kwargs.pop('font', ('Segoe UI', 9 if muted else 10)),
                        anchor='w', justify='left', **kwargs)

    @staticmethod
    def _button(parent, text, command, *, accent=False):
        return ttk.Button(parent, text=text, command=command,
                          style='Queue.Accent.TButton' if accent else 'Queue.TButton')

    def _build(self):
        outer = tk.Frame(self.window, background=COLORS['bg'])
        outer.pack(fill='both', expand=True, padx=16, pady=14)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        self._label(outer, 'Munkasor', font=('Segoe UI', 16, 'bold')).grid(
            row=0, column=0, sticky='ew')
        self.hint = self._label(outer,
            'Több videó egymás után, helyben és ingyen. A gép maradjon bekapcsolva; '
            'a munkasor ablakát közben bezárhatod.', muted=True, wraplength=900)
        self.hint.grid(row=1, column=0, sticky='ew', pady=(4, 9))

        source = tk.Frame(outer, background=COLORS['bg'])
        source.grid(row=2, column=0, sticky='ew', pady=(0, 9))
        source.columnconfigure(2, weight=1)
        self.add_button = self._button(source, 'Aktuális videó hozzáadása', self.add_current)
        self.add_button.grid(row=0, column=0, padx=(0, 7), sticky='w')
        self.import_button = self._button(source, 'Mentett projektek hozzáadása…', self.import_projects)
        self.import_button.grid(row=0, column=1, padx=(0, 10), sticky='w')
        self.count_label = self._label(source, '', muted=True)
        self.count_label.grid(row=0, column=2, sticky='e')

        table = tk.Frame(outer, background=COLORS['bg'])
        table.grid(row=3, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('number', 'title', 'state', 'progress'),
                                 show='headings', selectmode='browse', style='Queue.Treeview')
        for key, title, width, minimum, anchor, stretch in (
                ('number', '#', 44, 36, 'e', False),
                ('title', 'Videó', 430, 230, 'w', True),
                ('state', 'Állapot', 135, 110, 'w', False),
                ('progress', 'Haladás', 90, 76, 'e', False)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=minimum, anchor=anchor, stretch=stretch)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind('<<TreeviewSelect>>', self._selected)
        self.tree.bind('<Double-1>', lambda _event: self.open_result())

        edits = tk.Frame(outer, background=COLORS['bg'])
        edits.grid(row=4, column=0, sticky='ew', pady=(7, 7))
        edits.columnconfigure(5, weight=1)
        self.up_button = self._button(edits, '↑ Fel', lambda: self.move(-1))
        self.down_button = self._button(edits, '↓ Le', lambda: self.move(1))
        self.remove_button = self._button(edits, 'Eltávolítás', self.remove)
        self.retry_button = self._button(edits, 'Újrapróbálás', self.retry)
        self.result_button = self._button(edits, 'Eredmény megnyitása', self.open_result)
        for column, button in enumerate((self.up_button, self.down_button, self.remove_button,
                                         self.retry_button, self.result_button)):
            button.grid(row=0, column=column, sticky='w', padx=(0, 7))

        detail_frame = tk.Frame(outer, background=COLORS['bg'])
        detail_frame.grid(row=5, column=0, sticky='ew')
        detail_frame.columnconfigure(0, weight=1)
        self.details = tk.Text(detail_frame, height=3, wrap='word', background=COLORS['field'],
            foreground=COLORS['text'], insertbackground=COLORS['text'],
            selectbackground=COLORS['selection'], selectforeground=COLORS['text'],
            relief='flat', borderwidth=0, padx=8, pady=6, font=('Segoe UI', 9), state='disabled')
        self.details.grid(row=0, column=0, sticky='ew')
        detail_scroll = ttk.Scrollbar(detail_frame, orient='vertical', command=self.details.yview)
        detail_scroll.grid(row=0, column=1, sticky='ns')
        self.details.configure(yscrollcommand=detail_scroll.set)

        self.status = self._label(outer, '', wraplength=900)
        self.status.grid(row=6, column=0, sticky='ew', pady=(7, 5))
        self.progress = ttk.Progressbar(outer, maximum=100, mode='determinate',
                                        style='Queue.Horizontal.TProgressbar')
        self.progress.grid(row=7, column=0, sticky='ew', pady=(0, 9))

        actions = tk.Frame(outer, background=COLORS['bg'])
        actions.grid(row=8, column=0, sticky='ew')
        actions.columnconfigure(3, weight=1)
        self.start_button = self._button(actions, 'Munkasor indítása', self.start, accent=True)
        self.stop_button = self._button(actions, 'Leállás a jelenlegi után', self.stop_after_current)
        self.cancel_button = self._button(actions, 'Jelenlegi megszakítása', self.cancel_current)
        self.close_button = self._button(actions, 'Bezárás', self.close)
        for column, button in enumerate((self.start_button, self.stop_button, self.cancel_button)):
            button.grid(row=0, column=column, sticky='w', padx=(0, 7))
        self.close_button.grid(row=0, column=4, sticky='e')
        self.window.bind('<Configure>', self._resize, add='+')

    def _resize(self, event):
        if event.widget is self.window:
            width = max(660, event.width - 36)
            self.hint.configure(wraplength=width)
            self.status.configure(wraplength=width)

    def _selected_job(self):
        selected = self.tree.selection()
        if selected:
            return next((job for job in self._jobs if str(job['id']) == selected[0]), None)
        return None

    def _selected(self, _event=None):
        self._update_details()
        self._controls()

    @staticmethod
    def _progress_value(job):
        try:
            return max(0.0, min(100.0, float(job.get('progress') or 0)))
        except (TypeError, ValueError):
            return 0.0

    def _update_details(self):
        job = self._selected_job()
        if job:
            project = job.get('project') or {}
            text = f"{job.get('title', 'Videó')} • {STATUS_NAMES.get(job.get('status'), 'Ismeretlen állapot')}"
            text += f"\n{len(project.get('images') or [])} kép · {len(project.get('videos') or [])} videó"
            for key in ('message', 'error', 'result'):
                if job.get(key):
                    text += '\n' + str(job[key])
        else:
            text = 'Adj hozzá videókat, majd indítsd el a munkasort. A beállításokat minden feladathoz külön elmentjük.'
        if text != self._detail_signature:
            self._detail_signature = text
            self.details.configure(state='normal')
            self.details.delete('1.0', 'end')
            self.details.insert('1.0', text)
            self.details.configure(state='disabled')

    @staticmethod
    def _enable(widget, enabled):
        widget.state(['!disabled'] if enabled else ['disabled'])

    def _controls(self):
        job = self._selected_job()
        running = bool(self.runner.is_running)
        pending = [item for item in self._jobs if item.get('status') == 'pending']
        movable = bool(job and job.get('status') == 'pending')
        index = self._jobs.index(job) if movable else -1
        self._enable(self.add_button, self.get_project is not None)
        self._enable(self.start_button, not running and bool(pending))
        self._enable(self.stop_button, running and not self._stop_requested)
        self._enable(self.cancel_button, running and any(item.get('status') == 'running' for item in self._jobs))
        self._enable(self.up_button, movable and index > 0
                     and self._jobs[index - 1].get('status') != 'running')
        self._enable(self.down_button, movable and index < len(self._jobs) - 1
                     and self._jobs[index + 1].get('status') != 'running')
        self._enable(self.remove_button, bool(job and job.get('status') != 'running'))
        self._enable(self.retry_button, bool(job and job.get('status') in RETRY_STATUSES))
        self._enable(self.result_button, bool(job and job.get('status') == 'complete' and job.get('result')))

    def _set_notice(self, text):
        self._notice = str(text)
        self._notice_until = time.monotonic() + 8
        self.refresh()

    def refresh(self):
        if self._destroyed:
            return
        try:
            jobs = self.store.items()
        except Exception as error:
            self.status.configure(text='A munkasor nem olvasható: ' + str(error))
            return
        self._jobs = jobs
        signature = tuple((str(job['id']), str(job.get('title', 'Videó')), job.get('status'),
                           self._progress_value(job)) for job in jobs)
        if signature != self._signature:
            self._signature = signature
            old_selection = self.tree.selection()
            top = self.tree.yview()[0]
            ids = {str(job['id']) for job in jobs}
            for iid in self.tree.get_children():
                if iid not in ids:
                    self.tree.delete(iid)
            for index, job in enumerate(jobs):
                iid = str(job['id'])
                progress = self._progress_value(job)
                values = (index + 1, job.get('title', 'Videó'),
                          STATUS_NAMES.get(job.get('status'), 'Ismeretlen'), f'{progress:.0f}%')
                if self.tree.exists(iid):
                    self.tree.item(iid, values=values)
                    self.tree.move(iid, '', index)
                else:
                    self.tree.insert('', 'end', iid=iid, values=values)
            if old_selection and old_selection[0] in ids:
                self.tree.selection_set(old_selection)
            elif jobs:
                self.tree.selection_set(str(jobs[0]['id']))
            self.tree.yview_moveto(top)
        counts = Counter(job.get('status') for job in jobs)
        self.count_label.configure(text=f"{len(jobs)} videó · {counts['complete']} kész")
        running = bool(self.runner.is_running)
        active = next((job for job in jobs if job.get('status') == 'running'), None)
        if self._was_running and not running:
            self._notice = ('A munkasor megállt.' if counts['pending'] else 'A munkasor végére ért.')
            self._stop_requested = False
        self._was_running = running
        if active:
            text = f"Készül: {str(active.get('title', 'Videó'))[:90]}"
            if self._stop_requested:
                text += ' · Utána leáll.'
            active_notice = (self._notice if time.monotonic() < self._notice_until
                             else active.get('message', ''))
            if active_notice:
                text += '\n' + str(active_notice)[:180]
            self.progress['value'] = self._progress_value(active)
        else:
            text = self._notice or ('A munkasor indul…' if running else
                'A várakozó videók sorrendben készülnek el. Indításhoz kattints a Munkasor indítása gombra.')
            self.progress['value'] = 100 if jobs and counts['complete'] == len(jobs) else 0
        if getattr(self.runner, 'last_error', ''):
            text = 'A munkasor leállt: ' + str(self.runner.last_error)[:300]
        self.status.configure(text=text)
        self._update_details()
        self._controls()

    def _poll(self):
        self._after = None
        if self._destroyed:
            return
        self.refresh()
        self._after = self.window.after(200, self._poll)

    def add_current(self):
        if self.get_project is None:
            return
        try:
            project = self.get_project()
            if project is None:
                return
            job = self.store.add(project)
        except Exception as error:
            self._set_notice('Nem került a munkasorba: ' + str(error))
            return
        self._set_notice('Hozzáadva: ' + str(job.get('title', 'Videó')))
        self.tree.selection_set(str(job['id']))
        self.tree.see(str(job['id']))
        self._selected()

    def import_projects(self):
        selected = filedialog.askopenfilenames(parent=self.window,
            title='Mentett videóprojektek hozzáadása',
            filetypes=[('Videóprojekt', '*.json'), ('Minden fájl', '*.*')])
        if selected:
            self.add_project_files(selected)

    def add_project_files(self, paths):
        """Import each selected project separately; malformed files do not stop the batch."""
        added = []
        errors = []
        for value in paths:
            path = Path(value)
            try:
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError('A projektfájl túl nagy (legfeljebb 8 MB).')
                project = json.loads(path.read_text(encoding='utf-8-sig'))
                if not isinstance(project, dict):
                    raise ValueError('A fájl nem videóprojektet tartalmaz.')
                added.append(self.store.add(project, base=path.absolute().parent))
            except Exception as error:
                errors.append(path.name + ': ' + str(error))
        notice = f'{len(added)} projekt hozzáadva.'
        if errors:
            notice += f' {len(errors)} fájl nem adható hozzá: ' + ' | '.join(errors[:2])
        self._set_notice(notice)
        if added:
            self.tree.selection_set(str(added[-1]['id']))
            self.tree.see(str(added[-1]['id']))
            self._selected()
        return len(added), errors

    def _mutate_selected(self, method, *args):
        job = self._selected_job()
        if job is None:
            return
        try:
            getattr(self.store, method)(job['id'], *args)
        except Exception as error:
            self._set_notice('A művelet nem sikerült: ' + str(error))
            return
        self._notice = ''
        self.refresh()

    def move(self, delta):
        self._mutate_selected('move', delta)

    def remove(self):
        self._mutate_selected('remove')

    def retry(self):
        self._mutate_selected('retry')

    def start(self):
        if self.runner.is_running:
            return
        allowed = False
        try:
            if self.on_start and not self.on_start():
                self._set_notice('Előbb várd meg vagy állítsd le a főablakban futó videókészítést.')
                return
            allowed = True
            started = self.runner.start()
        except Exception as error:
            if allowed:
                self._start_failed()
            self._set_notice('A munkasor nem indult el: ' + str(error))
            return
        if not started:
            self._start_failed()
        self._stop_requested = False
        self._set_notice('A munkasor elindult.' if started else 'Nincs indítható várakozó videó.')

    def _start_failed(self):
        if self.on_start_failed:
            try:
                self.on_start_failed()
            except Exception as error:
                self._notice = 'A főablak állapotának frissítése nem sikerült: ' + str(error)

    def stop_after_current(self):
        try:
            self.runner.stop_after_current()
        except Exception as error:
            self._set_notice('A leállás kérése nem sikerült: ' + str(error))
            return
        self._stop_requested = True
        self._set_notice('A jelenlegi videó elkészül, utána leáll a munkasor.')

    def cancel_current(self):
        try:
            self.runner.cancel_current()
        except Exception as error:
            self._set_notice('A megszakítás nem sikerült: ' + str(error))
            return
        self._set_notice('A jelenlegi videó megszakítása folyamatban…')

    def open_result(self):
        job = self._selected_job()
        if not job or job.get('status') != 'complete' or not job.get('result'):
            return
        try:
            result = Path(job['result'])
            if not result.exists():
                raise ValueError('Az elkészült videó már nem található az eredeti helyén.')
            os.startfile(str(result))
        except Exception as error:
            self._set_notice('Az eredmény nem nyitható meg: ' + str(error))

    def close(self):
        """The app owns the runner: this action only closes its view."""
        if not self._destroyed:
            self.window.destroy()

    def _external_destroy(self, event):
        if event.widget is self.window:
            self._destroyed = True
            if self._after is not None:
                try:
                    self.window.after_cancel(self._after)
                except tk.TclError:
                    pass
                self._after = None

    def diagnostics(self):
        def info(widget):
            return {'mapped': bool(widget.winfo_ismapped()),
                    'bounds': [widget.winfo_rootx() - self.window.winfo_rootx(),
                               widget.winfo_rooty() - self.window.winfo_rooty(),
                               widget.winfo_width(), widget.winfo_height()],
                    'enabled': not widget.instate(['disabled'])}
        return {'window': [self.window.winfo_width(), self.window.winfo_height()],
                'palette': dict(COLORS), 'job_count': len(self.tree.get_children()),
                'states': dict(Counter(job.get('status') for job in self._jobs)),
                'runner_active': bool(self.runner.is_running),
                'start': info(self.start_button), 'stop': info(self.stop_button),
                'cancel': info(self.cancel_button), 'add': info(self.add_button),
                'import': info(self.import_button), 'retry': info(self.retry_button),
                'remove': info(self.remove_button), 'result': info(self.result_button),
                'up': info(self.up_button), 'down': info(self.down_button),
                'close': info(self.close_button)}


def ui_check(report_path):
    """Exercise the actual 900×620 Tk window without media, models or network."""
    class Store:
        def __init__(self):
            self.jobs = [{'id': str(index), 'title': title,
                'status': status, 'project': {'images': [], 'videos': []},
                'progress': 100 if status == 'complete' else 0,
                'message': '', 'error': '', 'result': ''}
                for index, (title, status) in enumerate((
                    ('Első éjszakai videó', 'pending'), ('Második éjszakai videó', 'pending'),
                    ('Harmadik videó', 'failed'), ('Elkészült videó', 'complete')))]

        def items(self):
            return copy.deepcopy(self.jobs)

        def add(self, project, base=None):
            job = {'id': str(len(self.jobs)), 'title': project['title'],
                   'status': 'pending', 'project': project, 'progress': 0,
                   'message': '', 'error': '', 'result': ''}
            self.jobs.append(job)
            return copy.deepcopy(job)

        def move(self, job_id, delta):
            index = next(i for i, job in enumerate(self.jobs) if job['id'] == job_id)
            target = index + delta
            self.jobs[index], self.jobs[target] = self.jobs[target], self.jobs[index]

        def retry(self, job_id):
            next(job for job in self.jobs if job['id'] == job_id)['status'] = 'pending'

        def remove(self, job_id):
            self.jobs = [job for job in self.jobs if job['id'] != job_id]

    class Runner:
        is_running = False
        active_engine = None
        starts = stops = cancels = 0

        def start(self):
            self.starts += 1
            self.is_running = True
            return True

        def stop_after_current(self):
            self.stops += 1

        def cancel_current(self):
            self.cancels += 1

    root = tk.Tk()
    root.withdraw()
    root.attributes('-alpha', 0)
    ttk.Style(root).theme_use('clam')
    store, runner = Store(), Runner()
    guard_calls = []
    window = show(root, store, runner, get_project=lambda: {'title': 'Új saját videó'},
                  on_start=lambda: guard_calls.append(True) or True, hidden=True)
    window.geometry('900x620')
    view = window.queue_view
    report = {}
    try:
        root.update()
        view.add_current()
        view.tree.selection_set('1')
        view.move(-1)
        reordered = store.jobs[0]['id'] == '1'
        view.tree.selection_set('2')
        view.retry()
        retried = next(job for job in store.jobs if job['id'] == '2')['status'] == 'pending'
        view.start_button.invoke()
        root.update()
        report = view.diagnostics()
        keys = ('start', 'stop', 'cancel', 'add', 'import', 'retry', 'remove',
                'result', 'up', 'down', 'close')
        visible = all(report[key]['mapped'] and report[key]['bounds'][0] >= 0
            and report[key]['bounds'][1] >= 0
            and report[key]['bounds'][0] + report[key]['bounds'][2] <= 900
            and report[key]['bounds'][1] + report[key]['bounds'][3] <= 620 for key in keys)
        view.stop_button.invoke()
        # Simulate a running snapshot solely to exercise the cancel control.
        store.jobs[0]['status'] = 'running'
        view.refresh()
        view.cancel_button.invoke()
        view.close()
        root.update()
        report.update({'real_hidden_tk': True, 'network_or_paid_requests': False,
            'actions_visible': visible, 'add_snapshot': len(store.jobs) == 5,
            'reordered': reordered, 'retried': retried,
            'start_guard_called': guard_calls == [True],
            'start_stop_cancel_called': (runner.starts, runner.stops, runner.cancels) == (1, 1, 1),
            'close_keeps_running': runner.is_running})
        report['status'] = 'PASS' if (visible and report['window'] == [900, 620]
            and all(report[key] for key in ('add_snapshot', 'reordered', 'retried',
                'start_guard_called', 'start_stop_cancel_called', 'close_keeps_running'))) else 'FAIL'
    except Exception as error:
        report.update({'status': 'FAIL', 'error': str(error), 'real_hidden_tk': True})
    finally:
        root.destroy()
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def diagnose_main(context, report_path):
    """Exercise real main-window callbacks with a deliberately fake renderer.

    This opt-in hidden-release diagnostic needs an isolated queue-file override.
    It tests form snapshots, ownership and Tk/worker communication, never video
    quality or model execution. The caller keeps ownership of the application.
    """
    from dataclasses import asdict, is_dataclass
    import threading
    import uuid
    from tkinter import messagebox
    from PIL import Image

    target = Path(report_path).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {'status': 'FAIL', 'real_hidden_tk': True, 'fake_engine': True,
              'media_rendered': False, 'network_or_paid_requests': False,
              'actual_main_enqueue': False, 'actual_main_open': False,
              'actual_main_start': False, 'snapshots_immutable': False,
              'busy_blocks_main_render': False, 'queue_opener_enabled_while_busy': False,
              'add_while_active': False, 'three_jobs_completed': False,
              'busy_cleared': False, 'close_keeps_store_alive': False,
              'main_snapshot_module': '', 'project_dataclass_module': '',
              'project_dataclass_name': '', 'errors': []}
    app = context['app']
    values = context['values']
    images = context['images']
    original_values = {key: value.get() for key, value in values.items()}
    original_images = list(images)
    videos = context.get('videos', [])
    original_videos = list(videos)
    text = context.get('text')
    original_text = text.get('1.0', 'end-1c') if text is not None else None
    original_text_state = text.cget('state') if text is not None else None
    gate = threading.Event()
    built = []
    old_engine_factory = None
    runner = None
    view = None
    dialogs = []
    previous_dialogs = {name: getattr(messagebox, name) for name in ('showerror', 'showinfo')}

    def pump_until(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.update()
            if predicate():
                return True
            time.sleep(.015)
        return False

    def text_set(value):
        if text is not None:
            text.configure(state='normal')
            text.delete('1.0', 'end')
            text.insert('1.0', value)

    class FakeEngine:
        def __init__(self, log, progress):
            self.log, self.progress = log, progress
            self.cancel = threading.Event()

        def build(self, project):
            entry = {'module': type(project).__module__,
                     'name': type(project).__name__, 'dataclass': is_dataclass(project),
                     'project': asdict(project)}
            built.append(entry)
            self.log('A főablak munkasor-kapcsolatának ellenőrzése.')
            self.progress(25)
            while not gate.wait(.025):
                if self.cancel.is_set():
                    raise RuntimeError('Megszakítva.')
            if self.cancel.is_set():
                raise RuntimeError('Megszakítva.')
            folder = Path(project.output) / f'ellenorzes_{len(built):02d}'
            folder.mkdir(parents=True, exist_ok=True)
            # A nonempty sentinel satisfies the runner's output contract.
            # It is explicitly not a playable media or a quality reference.
            (folder / 'video.mp4').write_bytes(b'QUEUE UI DIAGNOSTIC - NO VIDEO RENDERED\n')
            self.progress(100)
            return folder

    try:
        override = os.environ.get('YTMUHELY_QUEUE_FILE')
        if not override:
            raise ValueError('A főablak-próbához elkülönített munkasorfájl szükséges.')
        queue_path = Path(override).absolute()
        if target.parent not in queue_path.parents:
            raise ValueError('A próba munkasorfájlja az ellenőrzési mappán belül legyen.')
        folder = target.parent / ('main_queue_' + uuid.uuid4().hex[:10])
        folder.mkdir()
        picture = folder / 'kep.png'
        Image.new('RGB', (16, 16), '#94724C').save(picture)
        output = folder / 'eredmenyek'
        output.mkdir()
        for name in previous_dialogs:
            setattr(messagebox, name,
                    lambda title, message, *args, _kind=name, **kwargs:
                    dialogs.append({'kind': _kind, 'title': str(title), 'message': str(message)}))
        for key, value in {'visual_mode': 'Képes diavideó', 'narration': 'Nincs narráció',
                'output': str(output), 'seconds': '1', 'music': '', 'audio': '',
                'title': 'Első munkasorpróba'}.items():
            values[key].set(value)
        images[:] = [str(picture)]
        videos[:] = []
        text_set('First immutable queue snapshot.')
        snapshot = context['snapshot']()
        report['main_snapshot_module'] = type(snapshot).__module__
        if not is_dataclass(snapshot):
            raise ValueError('A főablak nem Project adatpéldányt adott vissza.')
        context['enqueue_project']()
        store = context['queue_store'][0]
        runner = context['queue_runner'][0]
        if store is None or runner is None or len(store.items()) != 1:
            raise ValueError('Az első főablakbeli hozzáadás nem sikerült, vagy a munkasor nem üres.')
        first = copy.deepcopy(store.items()[0]['project'])
        values['title'].set('Második munkasorpróba')
        text_set('Second immutable queue snapshot.')
        context['enqueue_project']()
        jobs = store.items()
        report['actual_main_enqueue'] = len(jobs) == 2
        report['snapshots_immutable'] = (len(jobs) == 2
            and jobs[0]['project'] == first
            and jobs[1]['project']['title'] == 'Második munkasorpróba'
            and jobs[1]['project']['text'] == 'Second immutable queue snapshot.'
            and first['title'] == 'Első munkasorpróba'
            and first['text'] == 'First immutable queue snapshot.')
        context['open_queue']()
        manager = context['queue_window'][0]
        if manager is None or not manager.winfo_exists():
            raise ValueError('A főablak nem nyitotta meg a munkasor ablakát.')
        manager.attributes('-alpha', 0)
        manager.geometry('900x620')
        view = manager.queue_view
        report['actual_main_open'] = True
        old_engine_factory = runner.engine_factory
        runner.engine_factory = FakeEngine
        view.start()
        report['actual_main_start'] = bool(context['busy'][0] and runner.is_running)
        report['busy_blocks_main_render'] = bool(context['primary_button'].instate(['disabled']))
        report['queue_opener_enabled_while_busy'] = not context['queue_open_button'][0].instate(['disabled'])
        if not pump_until(lambda: bool(built) and any(job['status'] == 'running' for job in store.items())):
            raise TimeoutError('A munkasor nem indította el az első próbafeladatot.')
        values['title'].set('Harmadik munkasorpróba')
        # Tk variables retain settings even while the main form is locked.
        view.add_current()
        report['add_while_active'] = bool(runner.is_running and len(store.items()) == 3
            and store.items()[2]['project']['title'] == 'Harmadik munkasorpróba'
            and store.items()[0]['project'] == first)
        gate.set()
        if not pump_until(lambda: not runner.is_running and not context['busy'][0]):
            raise TimeoutError('A főablak nem kapta meg a munkasor befejezését.')
        final_jobs = store.items()
        report['three_jobs_completed'] = len(final_jobs) == 3 and all(
            job['status'] == 'complete' and Path(job['result']).is_file()
            and Path(job['result']).stat().st_size > 0 for job in final_jobs)
        report['busy_cleared'] = not context['busy'][0] and not context['primary_button'].instate(['disabled'])
        report['job_statuses'] = [job['status'] for job in final_jobs]
        report['job_titles'] = [job['title'] for job in final_jobs]
        report['built_count'] = len(built)
        report['all_outputs_unique'] = len({job['result'] for job in final_jobs}) == 3
        if built:
            report['project_dataclass_module'] = built[0]['module']
            report['project_dataclass_name'] = built[0]['name']
            report['worker_received_main_dataclasses'] = all(
                entry['module'] == '__main__' and entry['name'] == 'Project'
                and entry['dataclass'] for entry in built)
        else:
            report['worker_received_main_dataclasses'] = False
        view.close()
        view = None
        app.update()
        report['close_keeps_store_alive'] = len(store.items()) == 3 and not runner.is_running
        report['errors'] = dialogs + [job['error'] for job in final_jobs if job.get('error')]
        gates = ('actual_main_enqueue', 'actual_main_open', 'actual_main_start',
            'snapshots_immutable', 'busy_blocks_main_render', 'queue_opener_enabled_while_busy',
            'add_while_active', 'three_jobs_completed', 'busy_cleared',
            'close_keeps_store_alive', 'all_outputs_unique', 'worker_received_main_dataclasses')
        report['status'] = 'PASS' if all(report.get(key) for key in gates) and not report['errors'] else 'FAIL'
    except Exception as error:
        report['errors'].append(str(error))
    finally:
        gate.set()
        if runner is not None and runner.is_running:
            runner.cancel_current()
            pump_until(lambda: not runner.is_running and not context['busy'][0], timeout=5)
        if runner is not None and old_engine_factory is not None and not runner.is_running:
            runner.engine_factory = old_engine_factory
        if view is not None:
            view.close()
        for name, handler in previous_dialogs.items():
            setattr(messagebox, name, handler)
        for key, value in original_values.items():
            values[key].set(value)
        images[:] = original_images
        videos[:] = original_videos
        if text is not None:
            text_set(original_text)
            text.configure(state=original_text_state)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
