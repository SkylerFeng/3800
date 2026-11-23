"""Local speech adapters. Audio enters and leaves subprocesses over pipes."""
import asyncio
from contextlib import suppress
import io
from pathlib import Path
import shutil
import sys
import wave


class SpeechError(RuntimeError):
    pass


def wav_bytes(pcm, rate):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


async def process(command, payload, timeout):
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        raise SpeechError('语音程序未安装或无法启动') from None
    try:
        output, _ = await asyncio.wait_for(proc.communicate(payload), timeout)
    except asyncio.TimeoutError:
        raise SpeechError('语音处理超时，请重试') from None
    finally:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.communicate()
    if proc.returncode != 0:
        raise SpeechError('本地语音处理失败，请检查语音程序或模型配置')
    return output


class LocalSpeech:
    def __init__(self, config):
        self.config = config

    @property
    def tts_enabled(self):
        return self.config.tts_provider in ('espeak', 'piper', 'matcha')

    async def transcribe(self, recording):
        if self.config.asr_provider == 'mock':
            return '杯子在哪里？'
        if self.config.asr_provider != 'vosk' or not self.config.vosk_model_path:
            raise SpeechError('请配置本地语音识别模型')
        worker = Path(__file__).with_name('vosk_worker.py')
        output = await process(
            [sys.executable, str(worker), self.config.vosk_model_path],
            wav_bytes(recording.pcm, recording.sample_rate), self.config.asr_timeout_s)
        text = output.decode('utf-8').strip()
        if not text or len(text) > 500:
            raise SpeechError('未听清问题，请重新录音或改用文字')
        return text

    async def synthesize(self, text):
        if self.config.tts_provider == 'matcha':
            if (not self.config.matcha_model_dir or not Path(self.config.matcha_model_dir).is_dir()
                    or not Path(self.config.matcha_vocoder).is_file()):
                raise SpeechError('请配置 Matcha 普通话模型及声码器')
            return await process(
                [sys.executable, str(Path(__file__).with_name('matcha_worker.py')),
                 self.config.matcha_model_dir, self.config.matcha_vocoder, str(self.config.matcha_speed)],
                text.encode('utf-8'), self.config.tts_timeout_s)
        if self.config.tts_provider == 'piper':
            if not self.config.piper_model_path or not Path(self.config.piper_model_path).is_file():
                raise SpeechError('找不到 Piper 普通话模型，请检查 PIPER_MODEL_PATH')
            worker = Path(__file__).with_name('piper_worker.py')
            audio = await process(
                [sys.executable, str(worker), self.config.piper_model_path,
                 str(self.config.piper_length_scale), str(self.config.piper_sentence_pause_s)],
                text.encode('utf-8'), self.config.tts_timeout_s)
            try:
                with wave.open(io.BytesIO(audio), 'rb') as wav:
                    if wav.getnframes() == 0:
                        raise ValueError()
            except (wave.Error, EOFError, ValueError):
                raise SpeechError('Piper 未生成可播放音频') from None
            return audio
        if self.config.tts_provider != 'espeak':
            raise SpeechError('本地语音合成未启用')
        binary = shutil.which(self.config.tts_binary)
        if not binary:
            raise SpeechError('找不到 eSpeak NG，请检查 TTS_BINARY')
        audio = await process(
            [binary, '--stdout', '-b', '1', '-v', self.config.tts_voice,
             '-s', str(self.config.tts_speed), '--stdin'],
            text.encode('utf-8'), self.config.tts_timeout_s)
        try:
            # Streaming WAV may use placeholder lengths; rebuild for browser playback.
            with wave.open(io.BytesIO(audio), 'rb') as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                    raise ValueError()
                return wav_bytes(wav.readframes(len(audio) // 2), wav.getframerate())
        except (wave.Error, EOFError, ValueError):
            raise SpeechError('语音合成未返回可播放音频') from None
