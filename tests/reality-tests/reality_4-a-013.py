"""Reality test for Fix 4-a-013: voice.py temp file leak on exception.

Behavioral execution test: verifies that VoiceHandler unlinks temporary files
even when transcription raises an exception (no temp file leaks in finally block).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class FailingWhisperModel:
    def __init__(self, failure_fn):
        self.transcribe = failure_fn


class ConcreteWhisperModule:
    def __init__(self, model):
        self._model = model

    def load_model(self, *args, **kwargs):
        return self._model


def test_transcribe_cleans_up_temp_file_on_exception():
    from scp.capabilities.voice import VoiceHandler

    handler = VoiceHandler()
    captured_paths = []

    def mock_transcribe_failure(path, **kwargs):
        captured_paths.append(path)
        assert os.path.exists(path), f"Temp file should exist during transcribe execution: {path}"
        raise RuntimeError("Simulated ASR transcription failure")

    model = FailingWhisperModel(mock_transcribe_failure)
    concrete_whisper = ConcreteWhisperModule(model)
    handler._whisper_model = model

    orig_whisper = sys.modules.get("whisper")
    sys.modules["whisper"] = concrete_whisper
    try:
        # Run transcribe with dummy bytes
        res = handler.transcribe(b"RIFF....WAVEfmt ....data....")
        assert res == ""
    finally:
        if orig_whisper is not None:
            sys.modules["whisper"] = orig_whisper
        else:
            sys.modules.pop("whisper", None)

    # Verify that the temp file was unlinked in the finally block
    assert len(captured_paths) == 1
    tmp_path = captured_paths[0]
    assert not os.path.exists(tmp_path), f"Temp file leaked after exception: {tmp_path}"

if __name__ == "__main__":
    test_transcribe_cleans_up_temp_file_on_exception()
    print("PASS: reality_4-a-013 behavioral tests passed")
