"""The safe text path must not force expensive VAE work onto the CPU."""
from pathlib import Path
from types import SimpleNamespace
import shutil,sys,unittest,uuid
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'work/packages'),str(ROOT/'work/v2_source')]
import comfy_video

class LaunchProfileTests(unittest.TestCase):
    def test_owned_server_uses_split_attention_and_keeps_vae_on_gpu(self):
        parent=ROOT/'work/temp';parent.mkdir(parents=True,exist_ok=True)
        root=parent/('profile_'+uuid.uuid4().hex);root.mkdir()
        try:
            engine=SimpleNamespace(check=Mock(),log=Mock())
            process=Mock();process.poll.return_value=None
            with patch.object(comfy_video,'_portable',return_value=root/'portable'),\
                 patch.object(comfy_video,'_free_port',return_value=8199),\
                 patch.object(comfy_video,'_probe',return_value=True),\
                 patch.object(comfy_video.subprocess,'Popen',return_value=process) as popen:
                try:
                    self.assertEqual(comfy_video._ensure_server(engine,root,force_owned=True),'http://127.0.0.1:8199')
                    command=popen.call_args.args[0]
                    for flag in ('--use-split-cross-attention','--disable-async-offload','--disable-pinned-memory'):
                        self.assertIn(flag,command)
                    self.assertNotIn('--use-pytorch-cross-attention',command)
                    self.assertNotIn('--cpu-vae',command)
                    self.assertEqual(command[command.index('--listen')+1],'127.0.0.1')
                    self.assertEqual(command[command.index('--port')+1],'8199')
                    self.assertTrue((root/'youtube_muhely_models.yaml').is_file())
                finally:
                    if getattr(engine,'_comfy_log_handle',None):engine._comfy_log_handle.close()
        finally:
            assert root.resolve().is_relative_to(parent.resolve())
            shutil.rmtree(root)

if __name__=='__main__':unittest.main()
