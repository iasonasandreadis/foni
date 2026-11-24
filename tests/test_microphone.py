"""Starting and stopping the microphone, including the time macOS's audio froze while stopping it
(Foni then stayed "recording" for good, with your sound muted)."""
import threading
import time
import types

import numpy as np
import pytest

import foni.audio as audio
from foni.app import Foni


class FakeStream:
    """A pretend microphone. `freeze=True` makes stop() hang, like macOS's audio did."""
    made = []

    def __init__(self, callback, freeze=False, **kw):
        self.callback, self.freeze = callback, freeze
        self.started_at = self.stopped_at = None
        self.release = threading.Event()
        FakeStream.made.append(self)

    def start(self):
        self.started_at = time.monotonic()
        self.callback(np.full((640, 1), 0.01, dtype=np.float32), 640, None, None)  # first audio

    def stop(self):
        self.stopped_at = time.monotonic()
        if self.freeze:
            self.release.wait(30)

    def close(self):
        pass


@pytest.fixture
def mic(monkeypatch):
    FakeStream.made = []
    freeze = {"on": False}
    monkeypatch.setattr(audio.sd, "InputStream",
                        lambda callback, **kw: FakeStream(callback, freeze["on"], **kw))
    monkeypatch.setattr(audio.Recorder, "STOP_WAIT", 0.3)
    yield audio.Recorder(), freeze
    for s in FakeStream.made:
        s.release.set()


def test_records_and_stops(mic):
    rec, _ = mic
    rec.start()
    out = rec.stop()
    assert len(out) == 640 and not rec.wedged


def test_never_stops_the_microphone_right_after_starting_it(mic):
    rec, _ = mic
    rec.start()          # a quick tap: stop asked for straight away
    rec.stop()
    s = FakeStream.made[-1]
    assert s.stopped_at - s.started_at >= audio.Recorder.MIN_AGE - 0.01


def test_a_frozen_stop_gives_up_keeps_the_audio_and_refuses_to_start_again(mic):
    rec, freeze = mic
    freeze["on"] = True
    rec.start()
    t = time.monotonic()
    out = rec.stop()
    assert time.monotonic() - t < audio.Recorder.MIN_AGE + audio.Recorder.STOP_WAIT + 0.3
    assert rec.wedged and len(out) == 640      # what you said is still typed
    with pytest.raises(RuntimeError):
        rec.start()                             # no new recording on a stuck microphone


def test_foni_unmutes_and_restarts_after_a_frozen_microphone(mic, monkeypatch):
    rec, freeze = mic
    freeze["on"] = True
    restored = []
    app = types.SimpleNamespace(recorder=rec, state="recording", restart_when_idle=False,
                                muter=types.SimpleNamespace(restore=lambda: restored.append(1)))
    rec.start()
    Foni._stop_recording(app, "idle")
    assert app.state == "idle" and restored and app.restart_when_idle
