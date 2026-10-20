# Harmadik féltől származó összetevők

A csomag forráskódot, licenceket és modellkártyát tartalmaz. A program saját MIT licence nem írja felül az alábbi összetevők feltételeit.

- Python 3.12: PSF License, https://www.python.org/downloads/source/
- Pillow: HPND, https://github.com/python-pillow/Pillow
- imageio-ffmpeg 0.6.0: BSD-2-Clause wrapper, https://github.com/imageio/imageio-ffmpeg/tree/v0.6.0
- FFmpeg 7.1: a mellékelt Windows-build GPL-komponenseket tartalmaz. A pontos licencet és konfigurációt az `FFMPEG_LICENSE.txt` tartalmazza. Forrás: https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz ; a imageio által csomagolt build forrása és fordítási szkriptje: https://github.com/imageio/imageio-binaries/tree/master/ffmpeg .
- Piper Windows 2023.11.14-2: MIT, licenc a `hangmotor/PIPER_LICENSE.md` fájlban. Pontos kiadás és forrás: https://github.com/rhasspy/piper/releases/tag/2023.11.14-2
- Piper phonemize / eSpeak NG / ONNX Runtime: az eredeti csomag összetevői. Piper phonemize forrás: https://github.com/rhasspy/piper-phonemize ; eSpeak NG (GPL-3.0) forrás: https://github.com/espeak-ng/espeak-ng ; ONNX Runtime (MIT): https://github.com/microsoft/onnxruntime .
- Angol női LJ Speech medium modell: https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/ljspeech/medium . A modellkártya `hangmotor/EN_MODEL_CARD` néven mellékelve. A kártya szerint a tanító adathalmaz public domain; forrás: https://keithito.com/LJ-Speech-Dataset/ .
- edge-tts 7.2.8: LGPL-3.0, https://github.com/rany2/edge-tts . Internetes, opcionális felolvasás.
- PyInstaller: GPL-2.0 a futtatókörnyezet terjesztését megengedő kivétellel, https://pyinstaller.org/en/stable/license.html .

A függőségek eredeti licencfájljai a csomag LICENSES mappájában is megtalálhatók. Az internetes szolgáltatások saját feltételei külön érvényesek.


## AI-videómotor

- ComfyUI v0.40.0 (GPL-3.0), AMD portable: https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.40.0
- ComfyUI-GGUF (Apache-2.0), pontos forrás: https://github.com/city96/ComfyUI-GGUF/tree/6ea2651e7df66d7585f6ffee804b20e92fb38b8a
- Wan2.2-TI2V-5B: Apache-2.0, eredeti modell https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B ; GGUF Q5_K_M https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF
- UMT5 XXL encoder Q5_K_M: https://huggingface.co/city96/umt5-xxl-encoder-gguf ; eredeti modell https://huggingface.co/google/umt5-xxl (Apache-2.0).
- Wan2.2 VAE: https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged (Apache-2.0).
- GGUF Python modul: https://github.com/ggml-org/llama.cpp/tree/master/gguf-py (MIT).
- Protocol Buffers: https://github.com/protocolbuffers/protobuf (BSD).
- PyTorch / AMD ROCm: https://github.com/pytorch/pytorch ; https://github.com/ROCm (az egyes komponensek saját licenceivel).
- 7-Zip standalone 7zr: LGPL/BSD, https://www.7-zip.org/license.txt ; forrás https://www.7-zip.org/download.html .
- A telepítőben használt eszközpróbához mellékelt stable-diffusion.cpp Vulkan: MIT, https://github.com/leejet/stable-diffusion.cpp/tree/1d0a58a .

A setup_ai.py és setup_comfy.py fájlok a pontos letöltési URL-eket és SHA-256 ellenőrzőösszegeket tartalmazzák. A nagy modellek és a ComfyUI forrása a külön AI_Motor mappában vannak.


## Élethű kép és mozgás

- Wan2.2 I2V A14B / LightX2V 260412 4-step: https://github.com/ModelTC/LightX2V ; GGUF forrás: https://huggingface.co/Abiray/Wan2.2-LightX2V-260412-4STEP-GGUF/tree/907bf4a2f422543d344982afb51ff6d14255c8d2 (Apache-2.0).
- Wan2.1 VAE: https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/ee6f4a40737a995bf5818954cfce6d59443b0f04 (Apache-2.0).
- GFPGAN 1.4: https://github.com/TencentARC/GFPGAN/tree/7552a7791caad982045a7bbe5634bbf1cd5c8679 . Apache-2.0 és a kapcsolódó StyleGAN2-licencek a face_restore_vendor/GFPGAN_LICENSE fájlban. A mellékelt két architektúrafájl a BasicSR regisztráció helyett helyi minimális regisztert használ; a modellel súlyszinten szigorúan ellenőrzött. A javítás egyszer, a forrásfotón fut.
- RIFE ncnn Vulkan 20221029, RIFE v4.6: https://github.com/nihui/rife-ncnn-vulkan/releases/tag/20221029 (MIT). A mozgássimító külön letöltés, a pontos SHA-256 a rife_asset.json fájlban.
- A realisztikus motor pontos modellcímei és SHA-256 ellenőrzőösszegei a realism_assets.json és face_restore_vendor/model_asset.json fájlokban találhatók. A modellek nem részei a program ZIP-jének.


## Helyi portrémotor

- LivePortrait, MIT, forrás: https://github.com/KlingAIResearch/LivePortrait/tree/9b294b3d0536135442ea73cb01e6cb3ca7029dd3 ; emberi modellek: https://huggingface.co/KlingTeam/LivePortrait/tree/6d116d1066c3539da1cea3189da91c46cb505584 .
- MediaPipe 1.1.0, Apache-2.0: https://github.com/google-ai-edge/mediapipe ; BlazeFace short-range v1 CPU-arcfelismerő. A portréfeldolgozás a MediaPipe Tasks API-t és a LivePortrait PyTorch-magját használja. InsightFace-modellt nem tölt le és nem használ.
- A külön portrait/packages mappa Python-kiegészítői a saját dist-info/licencfájljaikat tartalmazzák.
- A portrait_assets.json a pontos verziókat, letöltési címeket és SHA-256 ellenőrzőösszegeket rögzíti. A portrémodellek az AI_Motor/portrait mappában vannak, külön letöltésként.
