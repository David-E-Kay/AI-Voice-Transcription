import time
import numpy as np
import dictate


class FakeRecorder:
    def __init__(self, *a, **k):
        self.started = 0
        self.stopped = 0
        self._audio = np.zeros(16000, dtype=np.float32)  # 1 s -> not too short

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return self._audio


class FakeEngine:
    def __init__(self, *a, **k):
        pass

    def transcribe(self, audio):
        return "  hello world  "  # clean_text should normalize to "hello world"


def _wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _build_app(monkeypatch):
    calls = []
    monkeypatch.setattr(dictate, "Recorder", FakeRecorder)
    monkeypatch.setattr(dictate, "Engine", FakeEngine)
    monkeypatch.setattr(dictate, "inject", lambda t: calls.append(t))
    return dictate.App(), calls


def test_press_release_transcribes_and_injects(monkeypatch):
    app, calls = _build_app(monkeypatch)
    assert app.state == "IDLE"
    app.on_press()
    assert app.state == "RECORDING"
    assert app.recorder.started == 1
    app.on_release()
    assert _wait_until(lambda: app.state == "IDLE" and calls)
    assert calls == ["hello world"]  # transcription cleaned + injected
    assert app.recorder.stopped == 1


def test_repeated_press_is_noop_while_recording(monkeypatch):
    app, calls = _build_app(monkeypatch)
    app.on_press()
    app.on_press()  # key-repeat while held -> guard makes it a no-op
    assert app.recorder.started == 1


def test_release_while_idle_is_noop(monkeypatch):
    app, calls = _build_app(monkeypatch)
    app.on_release()  # never pressed
    assert app.state == "IDLE"
    assert app.recorder.stopped == 0
    assert calls == []


def test_short_clip_is_not_injected(monkeypatch):
    app, calls = _build_app(monkeypatch)
    app.recorder._audio = np.zeros(int(16000 * 0.1), dtype=np.float32)  # 100 ms -> too short
    app.on_press()
    app.on_release()
    assert _wait_until(lambda: app.state == "IDLE")
    assert calls == []  # too short -> nothing injected
