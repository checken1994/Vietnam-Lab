import os
import sys

os.environ.setdefault('SCP_API_PROFILE', 'full')
os.environ.setdefault('SCP_CAPABILITY_SECRET', 'dummy-secret-for-tests-123')
os.environ.setdefault('SCP_STORAGE_BACKEND', 'sqlite')

from scp.capabilities.voice import VoiceHandler


class ConcreteWhisperModel:
    def transcribe(self, audio_path, language=None):
        return {"text": "hello stt"}


class ConcreteWhisperModule:
    @staticmethod
    def load_model(name):
        return ConcreteWhisperModel()


def test_voice_handler_transcribe(monkeypatch):
    """FA-13: Cover capabilities/voice.py transcription logic with concrete objects."""
    vh = VoiceHandler()
    monkeypatch.setitem(sys.modules, "whisper", ConcreteWhisperModule())
    res = vh.transcribe(b"fake_audio", language="vi")
    assert res == "hello stt"

