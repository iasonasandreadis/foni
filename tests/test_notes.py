"""Conversation notes: listen to a conversation, then save its key points with the transcript."""
import queue
import time
import types

import numpy as np
import pytest

import foni.app as appmod
from foni.ai_local import AIEditor
from foni.app import Foni
from foni.config import DEFAULT_CONFIG
from foni.notes import save_notes, title_of
from foni.prompts import NOTES_SYSTEM


def test_the_ai_is_asked_for_key_points_not_a_cleanup():
    msgs = AIEditor._messages("notes", AIEditor._notes_input("so we meet friday"))
    assert msgs[0]["content"] == NOTES_SYSTEM and "Never invent" in NOTES_SYSTEM
    assert msgs[-1]["content"] == "<transcript>\nso we meet friday\n</transcript>"


def test_saves_key_points_and_the_whole_transcript(tmp_path):
    notes = "# Shoot plan: Saturday/Sunday?\n## Key points\n- Saturday after 10."
    path = save_notes(notes, "so are we still doing the shoot", time.mktime((2026, 11, 24, 12, 5, 0, 0, 0, -1)),
                      42.4, folder=tmp_path)
    assert path.name == "2026-11-24 12.05 Shoot plan Saturday Sunday.md"
    text = path.read_text()
    assert text.startswith(notes) and "42 min" in text and text.rstrip().endswith("so are we still doing the shoot")
    again = save_notes(notes, "x", time.mktime((2026, 11, 24, 12, 5, 0, 0, 0, -1)), 1, folder=tmp_path)
    assert again.name.endswith("(2).md")                      # never overwrites an earlier note


def test_without_an_ai_the_transcript_is_still_saved(tmp_path):
    path = save_notes(None, "hello there", time.time(), 0.2, folder=tmp_path)
    assert title_of(None) == "Conversation" and "hello there" in path.read_text()


def talking(seconds):
    """Speech-like audio: loud stretches with quiet gaps between them."""
    a = np.random.default_rng(0).normal(0, 0.1, 16000 * seconds).astype(np.float32)
    a[(np.arange(len(a)) // 8000) % 3 == 2] *= 0.01
    return a


@pytest.fixture
def foni(monkeypatch, tmp_path):
    """Foni's recording logic with a pretend microphone and a pretend AI."""
    monkeypatch.setattr(appmod, "play", lambda name, cfg: None)
    monkeypatch.setattr(appmod, "frontmost_app", lambda: "FaceTime")
    monkeypatch.setattr(appmod.threading, "Timer", lambda *a, **k: types.SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(appmod, "NOTES_DIR", tmp_path)
    monkeypatch.setattr(appmod, "save_notes", lambda *a, **k: save_notes(*a, folder=tmp_path, **k))
    opened = []
    monkeypatch.setattr(appmod.subprocess, "run", lambda cmd, **k: opened.append(cmd))
    muted = []
    app = types.SimpleNamespace(
        state="idle", cfg=dict(DEFAULT_CONFIG, tail_seconds=0), command=False, app="", rec_started=0,
        press_id=0, press_t=float("-inf"), last_tap=float("-inf"), latched_at=float("-inf"),
        swallow_release=False, jobs=queue.Queue(), events=queue.Queue(), notes=None, pending_toast=None,
        ahead={"press": None, "done": 0, "texts": []}, restart_when_idle=False,
        muter=types.SimpleNamespace(restore=lambda: None, mute=lambda **k: muted.append(1)),
        recorder=types.SimpleNamespace(wedged=False, start=lambda: None,
                                       stop=lambda: talking(90)),
        _hear=lambda audio, ahead=None: "so the launch is on friday and you will post the video",
        _get_ai=lambda: types.SimpleNamespace(notes=lambda t: "# Launch on Friday\n## Key points\n- Launch is Friday."))
    for m in ("_on_hotkey", "_finish", "_stop_recording", "_write_notes", "toggle_notes"):
        setattr(app, m, getattr(Foni, m).__get__(app))
    return app, opened, muted, tmp_path


def test_start_listen_stop_and_save(foni):
    app, opened, muted, folder = foni
    app._on_hotkey("notes_start", 1.0)
    assert app.state == "latched" and app.notes and not muted   # listening, sound left on
    app._on_hotkey("notes_stop", 5.0)
    audio, command, where, ahead, notes = app.jobs.get_nowait()
    assert notes is not None and app.notes is None and app.state == "working"
    app._write_notes(audio, ahead, notes)
    saved = list(folder.glob("*.md"))
    assert len(saved) == 1 and "Launch on Friday" in saved[0].name
    assert opened[-1][:2] == ["open", "-t"] and app.pending_toast == "Conversation notes saved"


def test_a_tap_of_your_hotkey_also_stops_it(foni):
    app, _, _, _ = foni
    app._on_hotkey("notes_start", 1.0)
    app._on_hotkey("press", 9.0, "left_control")
    assert app.jobs.get_nowait()[4] is not None


def test_not_while_you_are_dictating(foni):
    app, _, _, _ = foni
    app.state = "recording"
    app._on_hotkey("notes_start", 1.0)
    assert app.notes is None and "Finish this dictation first" in app.pending_toast


def test_the_menu_starts_and_stops_it(foni):
    app, _, _, _ = foni
    app.toggle_notes()
    assert app.events.get_nowait()[0] == "notes_start"
    app.notes = {"started": time.time()}
    app.toggle_notes()
    assert app.events.get_nowait()[0] == "notes_stop"


def test_unknown_word_markers_are_left_out_of_the_transcript(foni):
    app, _, _, folder = foni
    app._hear = lambda audio, ahead=None: "we film<unk> on  thursday"
    app._on_hotkey("notes_start", 1.0)
    app._on_hotkey("notes_stop", 5.0)
    audio, _, _, ahead, notes = app.jobs.get_nowait()
    app._write_notes(audio, ahead, notes)
    assert "we film on thursday" in next(folder.glob("*.md")).read_text()
