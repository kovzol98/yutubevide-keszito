import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v2_source'))
from comfy_progress import JobProgress

class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.job=JobProgress('http://127.0.0.1:8190','client','mine',lambda:self.now)
    def emit(self,kind,**data): self.job.accept({'type':kind,'data':data})
    def test_other_jobs_do_not_change_our_display(self):
        self.emit('executing',node='7',prompt_id='mine')
        self.emit('progress',node='7',value=2,max=20,prompt_id='other')
        self.assertEqual(self.job.value,0)
    def test_sampler_eta_is_frozen_between_real_step_updates(self):
        self.emit('executing',node='7',prompt_id='mine')
        self.now=186
        self.emit('progress',node='7',value=2,max=20,prompt_id='mine')
        text,percent=self.job.describe()
        self.assertIn('2/20',text); self.assertIn('28 perc',text)
        self.assertAlmostEqual(percent,11.5)
        self.now=220
        self.assertEqual(self.job.describe()[0],text)
    def test_decode_is_not_reported_as_finished_sampling(self):
        self.emit('progress',node='7',value=20,max=20,prompt_id='mine')
        self.emit('executing',node='9',prompt_id='mine')
        self.assertIn('véglegesítése',self.job.describe()[0])
        self.assertEqual(self.job.describe()[1],70)
        self.emit('progress',node='9',value=3,max=12,prompt_id='mine')
        self.assertIn('25%',self.job.describe()[0])
        self.assertEqual(self.job.describe()[1],75)
    def test_progress_bar_respects_scene_boundaries(self):
        import ytmuhely
        values=[]; engine=ytmuhely.Engine(progress=values.append)
        engine._scene_count=2; engine._scene_index=1
        engine.report_scene_progress(50)
        self.assertEqual(values[-1],67.5)
        engine.report_scene_progress(200)
        self.assertEqual(values[-1],90)
    def test_two_models_show_four_steps_and_loading_does_not_reset_bar(self):
        self.emit('ytmuhely_phase',node='7',prompt_id='mine',phase=1,status='sampling')
        self.emit('progress',node='7',prompt_id='mine',value=2,max=2)
        self.assertIn('2/4',self.job.describe()[0]); first=self.job.describe()[1]
        self.emit('ytmuhely_phase',node='7',prompt_id='other',phase=2,status='loading')
        self.assertEqual(first,self.job.describe()[1])
        self.emit('ytmuhely_phase',node='7',prompt_id='mine',phase=2,status='loading')
        self.assertIn('modell betöltése',self.job.describe()[0]);self.assertEqual(first,self.job.describe()[1])
        self.emit('ytmuhely_phase',node='7',prompt_id='mine',phase=2,status='sampling')
        self.emit('progress',node='7',prompt_id='mine',value=1,max=2)
        self.assertIn('3/4',self.job.describe()[0]);self.assertGreater(self.job.describe()[1],first)

class SocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_websocket_updates_and_closes_without_touching_queue(self):
        import asyncio
        from aiohttp import web
        connected=[]
        async def handler(request):
            connected.append(request.query['clientId'])
            socket=web.WebSocketResponse(); await socket.prepare(request)
            await socket.send_json({'type':'executing','data':{'node':'7','prompt_id':'mine'}})
            await socket.send_json({'type':'progress','data':{'node':'7','prompt_id':'mine','value':3,'max':20}})
            await socket.send_json({'type':'progress','data':{'node':'9','prompt_id':'other','value':1,'max':2}})
            async for message in socket: pass
            return socket
        app=web.Application(); app.router.add_get('/ws',handler)
        runner=web.AppRunner(app); await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
        port=site._server.sockets[0].getsockname()[1]
        job=JobProgress('http://127.0.0.1:%d'%port,'ours','mine')
        try:
            job.start()
            for _ in range(100):
                if job.value==3: break
                await asyncio.sleep(.02)
            self.assertEqual(connected,['ours'])
            self.assertIn('3/20',job.describe()[0])
        finally:
            await asyncio.to_thread(job.close); await runner.cleanup()
        self.assertFalse(job.thread.is_alive())

if __name__=='__main__': unittest.main()
