"""Launch a loopback demo with offline providers and optional local speech."""
import argparse
from importlib.util import find_spec
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    env = os.environ | {
        'CAMERA_SOURCE': 'mock',
        'TELEMETRY_SOURCE': 'mock',
        'ESP32_HOST': '',
        'WEB_ACCESS_PASSWORD': '',
        'VISION_PROVIDER': 'mock',
        'VISION_API_BASE_URL': '',
        'VISION_API_KEY': '',
        'VISION_API_MODEL': '',
        'ALLOW_EXTERNAL_VISION': 'false',
        'OLLAMA_MODEL': '',
        'MAP_PROVIDER': 'mock',
        'AMAP_API_KEY': '',
        'ALLOW_EXTERNAL_MAPS': 'false',
        'HAPTIC_OUTPUT': 'mock',
        'ASR_PROVIDER': 'unconfigured',
    }
    bundled_local = ROOT / 'artifacts/voice-tools/usr/bin/espeak-ng'
    tts = shutil.which(env.get('TTS_BINARY', 'espeak-ng'))
    if not tts and bundled_local.is_file():
        tts = str(bundled_local)
    env['TTS_PROVIDER'] = 'espeak' if tts else 'mock'
    if tts:
        env['TTS_BINARY'] = tts
    piper_model = ROOT / 'models/piper/zh_CN-huayan-medium.onnx'
    if find_spec('piper') and piper_model.is_file() and piper_model.with_suffix('.onnx.json').is_file():
        env['TTS_PROVIDER'] = 'piper'
        env['PIPER_MODEL_PATH'] = str(piper_model)
    print(f'离线演示 http://127.0.0.1:{args.port}/demo', flush=True)
    return subprocess.call(
        [sys.executable, str(ROOT / 'scripts/serve.py'), '--port', str(args.port)],
        cwd=ROOT, env=env,
    )


if __name__ == '__main__':
    raise SystemExit(main())
