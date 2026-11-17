"""Muting your sound while you dictate (with a pretend speaker, so the tests stay silent)."""
import json
import types

import pytest

from foni import speakers
from foni.app import Foni
from foni.speakers import SpeakerMuter


@pytest.fixture
def speaker(tmp_path, monkeypatch):
    monkeypatch.setattr(speakers, "MARK", tmp_path / "muted.json")
    state = {"muted": False, "calls": []}

    def silence(device):
        state["calls"].append("mute")
        if state["muted"]:
            return None          # already muted by you
        state["muted"] = True
        return {"device": device, "how": "mute"}

    def unsilence(undo):
        state["calls"].append("unmute")
        state["muted"] = False
    return state, SpeakerMuter((silence, unsilence, lambda: 73))


def test_mutes_and_puts_it_back(speaker):
    state, m = speaker
    m.mute()
    assert state["muted"] and speakers.MARK.exists()
    m.mute()                                   # a second call changes nothing
    assert state["calls"] == ["mute"]
    m.restore()
    assert not state["muted"] and not speakers.MARK.exists()
    m.restore()                                # nothing left to undo
    assert state["calls"] == ["mute", "unmute"]


def test_leaves_your_own_mute_alone(speaker):
    state, m = speaker
    state["muted"] = True                      # you muted it yourself before dictating
    m.mute()
    m.restore()
    assert state["muted"] and state["calls"] == ["mute"]


def test_no_mute_once_the_recording_has_ended(speaker):
    state, m = speaker
    m.mute(still_wanted=lambda: False)
    assert not state["muted"] and state["calls"] == []


def test_unmutes_after_foni_was_stopped_while_muted(speaker):
    state, m = speaker
    state["muted"] = True
    speakers.MARK.write_text(json.dumps({"device": 73, "how": "mute"}))
    SpeakerMuter((None, speaker[1].unsilence, None)).recover()
    assert not state["muted"] and not speakers.MARK.exists()


def test_finds_this_macs_speakers():
    assert isinstance(speakers.default_output(), int)   # reads only; changes nothing


@pytest.fixture
def dictation(speaker):
    state, m = speaker
    app = types.SimpleNamespace(press_id=1, state="recording", muter=m, recorder=types.SimpleNamespace(
        stop=lambda: "audio"), cfg={"sounds": False, "mute_while_dictating": True})
    for name in ("_start_cue", "_stop_recording"):
        setattr(app, name, getattr(Foni, name).__get__(app))
    return state, app


def test_mutes_while_you_hold_the_key(dictation):
    state, app = dictation
    app._start_cue(1)
    assert state["muted"]
    assert app._stop_recording("working") == "audio"
    assert not state["muted"] and app.state == "working"


def test_quick_shortcut_never_mutes(dictation):
    state, app = dictation
    app._stop_recording("idle")                # Ctrl+C: over before the start sound
    app._start_cue(1)
    assert state["calls"] == []


def test_mute_can_be_turned_off(dictation):
    state, app = dictation
    app.cfg["mute_while_dictating"] = False
    app._start_cue(1)
    assert state["calls"] == []
