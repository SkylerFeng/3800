"""Recognize one WAV locally in Mandarin using sherpa-onnx."""
import io
import sys
import wave

import numpy as np
import sherpa_onnx


def main():
    model, tokens, threads = sys.argv[1:]
    with wave.open(io.BytesIO(sys.stdin.buffer.read()), 'rb') as audio:
        rate = audio.getframerate()
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=model, tokens=tokens, num_threads=int(threads), language='zh', use_itn=True,
    )
    stream = recognizer.create_stream()
    stream.accept_waveform(rate, samples.astype(np.float32) / 32768)
    recognizer.decode_stream(stream)
    sys.stdout.buffer.write(stream.result.text.encode('utf-8'))


if __name__ == '__main__':
    main()
