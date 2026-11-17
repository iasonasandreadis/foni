"""The hotkey (including keys macOS forgets to report), menus, pills, settings and models."""
import json
import queue
import types

import pytest
from Quartz import CGEventCreateKeyboardEvent, CGEventSetFlags, CGEventSetType, kCGEventFlagsChanged, kCGEventKeyDown

import foni.config as config
import foni.models as models
from foni.app import Foni
from foni.config import DEFAULT_CONFIG
from foni.hotkeys import CMD, CTRL, FN
from foni.pills import PILL_STYLES


# ---- hotkey: press, release, shortcuts, and missed releases
@pytest.fixture
def keys():
    app = types.SimpleNamespace(keys_down=set(), events=queue.Queue(), learning=False,
                                hotkeys=["fn", "left_control"], command_mod=0x80000, tap=None)
    for m in ("_is_down", "_missed_release", "_on_event", "_check_stuck_keys"):
        setattr(app, m, getattr(Foni, m).__get__(app))

    def ev(kind, keycode, flags):
        e = CGEventCreateKeyboardEvent(None, keycode, kind != "up")
        if kind in ("mod", "up"):
            CGEventSetType(e, kCGEventFlagsChanged)
        CGEventSetFlags(e, flags)
        app._on_event(None, kCGEventFlagsChanged if kind in ("mod", "up") else kCGEventKeyDown, e, None)

    def drain():
        out = []
        while not app.events.empty():
            out.append(app.events.get()[0])
        return out
    return app, ev, drain


CTRL_L = CTRL | 0x01


def test_fn_and_control_dictation(keys):
    app, ev, drain = keys
    ev("mod", 63, FN), ev("up", 63, 0)
    assert drain() == ["press", "release"]
    ev("mod", 59, CTRL_L), ev("up", 59, 0)
    assert drain() == ["press", "release"]


def test_ctrl_shortcut_is_not_dictation(keys):
    app, ev, drain = keys
    ev("mod", 59, CTRL_L), ev("down", 8, CTRL_L)  # Ctrl+C
    assert drain() == ["press", "chord"]


def test_missed_release_from_screenshot_shortcut(keys):
    app, ev, drain = keys
    ev("mod", 59, CTRL_L)              # Control down, as part of Cmd+Ctrl+Shift+4
    drain()                            # ... macOS never reports Control going up ...
    ev("down", 9, CMD | 0x08)          # the next key you press (Cmd+V) shows Control is up
    assert drain() == ["chord"] and not app.keys_down
    ev("mod", 63, FN)                  # and Fn works again
    assert drain() == ["press"]


def test_keyboard_check_releases_stuck_control_but_not_fn(keys, monkeypatch):
    app, _, drain = keys
    monkeypatch.setattr("foni.app.CGEventSourceFlagsState", lambda state: 0)  # no keys held
    app.keys_down = {"left_control"}
    app._check_stuck_keys()
    assert drain() == ["chord"] and not app.keys_down
    app.keys_down = {"fn"}
    app._check_stuck_keys()
    assert drain() == [] and app.keys_down == {"fn"}


# ---- menus
@pytest.fixture
def menu_app(monkeypatch):
    saved = []
    monkeypatch.setattr("foni.app.save_config", lambda cfg: saved.append(json.loads(json.dumps(cfg))))
    app = types.SimpleNamespace(cfg=json.loads(json.dumps(DEFAULT_CONFIG)), saved=saved)
    for m in ("_build_writing_menu", "_refresh_writing_menu", "set_writing_style"):
        setattr(app, m, getattr(Foni, m).__get__(app))
    return app


def test_writing_style_menu(menu_app):
    app = menu_app
    app._build_writing_menu()
    assert [sub.title for sub, _, _ in app.writing_items.values()] == [
        "Personal messages: Casual", "Work messages: Formal", "Email: Formal", "Other apps: Formal"]
    sub, _, items = app.writing_items["personal"]
    app.set_writing_style(items["very_casual"])
    assert app.saved[-1]["app_styles"]["personal"] == "very_casual"
    assert sub.title == "Personal messages: Very casual"
    assert [k for k, i in items.items() if i.state] == ["very_casual"]


# ---- every pill style draws every state without errors
@pytest.mark.parametrize("style", sorted(PILL_STYLES))
def test_pill_styles(style):
    pill = PILL_STYLES[style]()
    pill.visible = True  # don't put a window on screen
    for mode in ("idle", "recording", "latched", "command", "working", "none", "idle"):
        for _ in range(3):
            pill.show(mode, [0.5] * 18)
    pill.hide()


# ---- settings move over from LocalFlow, once
def test_migrates_localflow_data(tmp_path, monkeypatch):
    old, new = tmp_path / ".localflow", tmp_path / ".foni"
    old.mkdir()
    new.mkdir()
    (new / "foni.log").write_text("")  # the app's launcher creates the folder for its log first
    (old / "config.json").write_text(json.dumps({"pill_style": "equalizer"}))
    (old / "dictionary.json").write_text('{"rabbits": {"to": "RAM", "count": 1}}')
    for name, value in (("OLD_APP_DIR", old), ("APP_DIR", new), ("CONFIG_PATH", new / "config.json")):
        monkeypatch.setattr(config, name, value)
    assert config.migrate_old_data() is True
    cfg = json.loads((new / "config.json").read_text())
    assert cfg["pill_style"] == "equalizer" and cfg["welcomed"] is True
    assert (new / "dictionary.json").exists()
    assert config.migrate_old_data() is False  # only once


# ---- models: use the downloaded copy, download only when missing
def test_model_path_uses_local_copy_first(monkeypatch):
    import huggingface_hub
    calls = []

    def fake(repo, local_files_only=False, **kw):
        calls.append(local_files_only)
        if local_files_only:
            return f"/cache/{repo}"
        raise AssertionError("should not download")
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake)
    assert models.model_path("org/model") == "/cache/org/model" and calls == [True]
