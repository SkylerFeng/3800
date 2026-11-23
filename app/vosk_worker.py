"""Isolated, cancellable offline recognition worker for a single WAV."""
import json
import sys
import wave

from vosk import KaldiRecognizer, Model, SetLogLevel

if __name__ == '__main__':
    SetLogLevel(-1)
    with wave.open(sys.stdin.buffer, 'rb') as audio:
        recognizer = KaldiRecognizer(Model(sys.argv[1]), audio.getframerate())
        segments = []
        while chunk := audio.readframes(4000):
            if recognizer.AcceptWaveform(chunk):
                segments.append(json.loads(recognizer.Result()).get('text', ''))
        segments.append(json.loads(recognizer.FinalResult()).get('text', ''))
    sys.stdout.buffer.write(''.join(segments).replace(' ', '').strip().encode('utf-8'))
