"""Run the Mandarin Matcha model locally and write PCM WAV to stdout."""
import io
from pathlib import Path
import sys
import wave

import numpy as np
import sherpa_onnx as sherpa


def main():
    directory, vocoder, speed = sys.argv[1:]
    root = Path(directory)
    model = sherpa.OfflineTtsMatchaModelConfig(
        acoustic_model=str(root / 'model-steps-3.onnx'),
        vocoder=vocoder,
        lexicon=str(root / 'lexicon.txt'),
        tokens=str(root / 'tokens.txt'),
    )
    config = sherpa.OfflineTtsConfig(
        model=sherpa.OfflineTtsModelConfig(matcha=model, num_threads=2),
        rule_fsts=','.join(str(root / name) for name in ('date.fst', 'number.fst', 'phone.fst')),
    )
    if not config.validate():
        raise ValueError('Invalid Matcha model configuration')
    audio = sherpa.OfflineTts(config).generate(sys.stdin.read().strip(), speed=float(speed))
    pcm = (np.clip(audio.samples, -1, 1) * 32767).astype('<i2')
    if not pcm.size:
        raise ValueError('No audio generated')
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, audio.sample_rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(pcm.tobytes())
    sys.stdout.buffer.write(output.getvalue())


if __name__ == '__main__':
    main()
