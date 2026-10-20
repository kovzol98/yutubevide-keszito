"""Read only this application's ComfyUI job progress over its own websocket."""
from __future__ import annotations
import asyncio, json, threading, time

class JobProgress:
    def __init__(self, base, client_id, prompt_id, clock=time.monotonic):
        self.base, self.client_id, self.prompt_id = base, client_id, prompt_id
        self.clock = clock; self.lock = threading.Lock(); self.stop = threading.Event()
        self.node = None; self.value = 0; self.total = 0; self.phase_start = clock()
        self.last_step_time = self.phase_start
        self.quality_phase = 0; self.quality_status = ''
        self.thread = None

    def accept(self, message):
        data = message.get('data', {})
        if data.get('prompt_id') not in (None, self.prompt_id): return
        kind = message.get('type')
        if kind not in ('executing', 'progress','ytmuhely_phase'): return
        if kind=='ytmuhely_phase' and data.get('prompt_id')!=self.prompt_id:return
        with self.lock:
            node = str(data['node']) if data.get('node') is not None else None
            if node != self.node:
                self.node = node; self.value = self.total = 0; self.phase_start = self.clock()
                self.quality_phase=0; self.quality_status=''
            if kind=='ytmuhely_phase':
                phase=data.get('phase');status=data.get('status')
                if phase in (1,2) and status in ('loading','sampling','complete'):
                    if phase!=self.quality_phase:self.value=self.total=0
                    self.quality_phase=phase;self.quality_status=status
            if kind == 'progress':
                value, total = data.get('value', 0), data.get('max', 0)
                if isinstance(value, (int, float)) and isinstance(total, (int, float)) and total > 0:
                    self.value = min(total, max(0, value)); self.total = total
                    self.last_step_time = self.clock()

    def describe(self):
        with self.lock:
            node, value, total, started, updated = self.node, self.value, self.total, self.phase_start, self.last_step_time
            phase,state=self.quality_phase,self.quality_status
        ratio = value / total if total else 0
        if node == '7':
            if phase:
                completed=(phase-1)*2+(2 if state=='complete' else (2*value/total if total else 0))
                label='Élethű jelenet: %d/2 szakasz · '%phase
                label+= 'modell betöltése' if state=='loading' else '%d/4 számítási lépés'%completed
                return label,5+65*completed/4
            label = 'Wan: %d/%d számítási lépés' % (value, total) if total else 'Wan: a jelenet számítása indul'
            if value >= 2 and ratio < 1:
                remaining = max(0, (updated - started) / value * (total - value))
                label += ' · becsült még %d perc + véglegesítés' % max(1, round(remaining / 60))
            return label, 5 + 65 * ratio
        if node == '9':
            label = 'Képkockák véglegesítése'
            if total: label += ': %d/%d rész (%d%%)' % (value, total, round(100 * ratio))
            return label, 70 + 20 * ratio
        if node == '10': return 'Az elkészült képkockák mentése…', 90
        if node in ('2', '5', '6'): return 'A jelenetleírás feldolgozása…', 2
        if node in ('1', '3', '4', '8', '11'): return 'Az AI-modellek és a referenciafotó előkészítése…', 3
        return '', None

    async def _listen(self):
        import aiohttp
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=None, sock_connect=5)) as session:
            async with session.ws_connect(self.base.replace('http:', 'ws:', 1) + '/ws?clientId=' + self.client_id, heartbeat=20) as socket:
                while not self.stop.is_set():
                    try: message = await socket.receive(timeout=1)
                    except asyncio.TimeoutError: continue
                    if message.type == aiohttp.WSMsgType.TEXT:
                        try: self.accept(json.loads(message.data))
                        except (ValueError, TypeError, AttributeError): pass
                    elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR): break

    def start(self):
        def run():
            try: asyncio.run(self._listen())
            except Exception: pass  # History polling still controls success/failure.
        self.thread = threading.Thread(target=run, daemon=True); self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None: self.thread.join(timeout=2)
