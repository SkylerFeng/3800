"""Synthesize Mandarin locally in a cancellable process."""
import io
import json
from pathlib import Path
import sys
import wave

from piper import PiperVoice, SynthesisConfig


def main():
    model, length_scale, sentence_pause = sys.argv[1:]
    config = json.loads(Path(model + '.json').read_text())
    # This preset uses local phonemization. Other models may fetch g2pW at runtime.
    if config.get('phoneme_type', 'espeak') != 'espeak':
        raise ValueError('This worker requires an offline eSpeak-phonemized Piper model')
    voice = PiperVoice.load(model)
    settings = SynthesisConfig(length_scale=float(length_scale))
    silence = b'\0\0' * round(float(sentence_pause) * voice.config.sample_rate)
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as output:
        output.setparams((1, 2, voice.config.sample_rate, 0, 'NONE', 'not compressed'))
        for index, chunk in enumerate(voice.synthesize(sys.stdin.read().strip(), settings)):
            if index:
                output.writeframesraw(silence)
            output.writeframesraw(chunk.audio_int16_bytes)
    sys.stdout.buffer.write(audio.getvalue())


if __name__ == '__main__':
    main()
