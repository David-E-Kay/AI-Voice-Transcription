import time
import queue
import ctypes
import numpy as np
import pytest
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


class FakeOverlay:
    def __init__(self, *a, **k):
        self.queue = queue.SimpleQueue()


class FakeVolume:
    def __init__(self):
        self.calls = []

    def SetMute(self, muted, _reserved):
        self.calls.append(muted)


def _bare_app():
    # ponytail: App.__init__ starts a real Recorder/Engine/Overlay and a real
    # _mute_worker daemon thread. None of that is wanted here, and a second consumer
    # racing the daemon thread for the same queue would make these tests flaky. Bypass
    # __init__ entirely so _mute_worker can be driven synchronously on the test thread.
    app = dictate.App.__new__(dictate.App)
    app._mute_q = queue.SimpleQueue()
    return app


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
    monkeypatch.setattr(dictate, "Overlay", FakeOverlay)
    monkeypatch.setattr(dictate, "inject", lambda t: calls.append(t))
    # Hermetic: without this, App() starts a real mute worker that makes real COM calls
    # against this machine's actual default speaker.
    monkeypatch.setattr(dictate, "_default_speaker_volume", lambda: FakeVolume())
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


def test_second_instance_exits():
    dictate._exit_if_already_running()  # first call takes the mutex
    try:
        with pytest.raises(SystemExit):
            dictate._exit_if_already_running()  # second call finds it held -> exits
    finally:
        ctypes.windll.kernel32.CloseHandle(dictate._instance_mutex)


def test_mute_resolves_default_device_each_time(monkeypatch):
    # Regression test: the endpoint used to be resolved once at startup and reused
    # forever, so a second dictation after the default output device changed kept
    # muting the OLD device. Assert the resolver is called once per mute leg (not once
    # per queue item) and that each fake only ever sees its own dictation's calls.
    app = _bare_app()
    fakes = [FakeVolume(), FakeVolume()]
    resolver_calls = []

    def fake_resolver():
        resolver_calls.append(1)
        return fakes[len(resolver_calls) - 1]

    monkeypatch.setattr(dictate, "_default_speaker_volume", fake_resolver)
    for item in (1, 0, 1, 0, None):
        app._mute_q.put(item)
    app._mute_worker()

    assert len(resolver_calls) == 2  # once per mute leg, not once per queue item
    assert fakes[0].calls == [1, 0]
    assert fakes[1].calls == [1, 0]  # fake #1 must NOT see the second dictation's mute


def test_unmute_targets_the_device_that_was_muted(monkeypatch):
    # Mid-dictation device-switch guard: if the default output changes between press
    # and release, unmute must hit the device that was actually muted, not whatever is
    # newly default.
    app = _bare_app()
    fake_a = FakeVolume()
    fake_b = FakeVolume()
    resolver = iter([fake_a, fake_b])
    monkeypatch.setattr(dictate, "_default_speaker_volume", lambda: next(resolver))

    app._mute_q.put(1)
    app._mute_q.put(0)
    app._mute_q.put(None)
    app._mute_worker()

    assert fake_a.calls == [1, 0]
    assert fake_b.calls == []  # never resolved because A was remembered for the unmute


def test_worker_survives_a_failing_endpoint(monkeypatch, caplog):
    app = _bare_app()
    working = FakeVolume()
    attempts = [0]

    def flaky_resolver():
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError("endpoint unavailable")
        return working

    monkeypatch.setattr(dictate, "_default_speaker_volume", flaky_resolver)
    for item in (1, 1, 0, None):
        app._mute_q.put(item)

    with caplog.at_level("ERROR"):
        app._mute_worker()  # must not raise / exit early

    assert working.calls == [1, 0]
    assert any("mute" in rec.getMessage().lower() for rec in caplog.records)


def test_worker_logs_and_exits_if_com_init_fails(monkeypatch, caplog):
    # If CoInitialize itself raises, the worker used to die silently with no log line
    # and muting would be dead for the whole process run -- the exact failure mode
    # that made the original bug take an hour to diagnose.
    app = _bare_app()
    resolver_calls = []
    monkeypatch.setattr(dictate, "_default_speaker_volume", lambda: resolver_calls.append(1))

    def failing_coinit():
        raise OSError("CoInitialize failed")

    monkeypatch.setattr(dictate.comtypes, "CoInitialize", failing_coinit)
    app._mute_q.put(1)  # never consumed -- worker must return before touching the queue

    with caplog.at_level("ERROR"):
        app._mute_worker()  # must not raise

    assert resolver_calls == []  # resolver never called
    assert any("com" in rec.getMessage().lower() for rec in caplog.records)
