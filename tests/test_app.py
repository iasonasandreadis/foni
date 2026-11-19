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
    for m in ("_build_writing_menu", "_refresh_writing_menu", "set_writing_style", "_style_setting",
              "toggle_styles_paused"):
        setattr(app, m, getattr(Foni, m).__get__(app))
    return app


def test_writing_style_menu(menu_app):
    app = menu_app
    app._build_writing_menu()
    assert [sub.title for sub, _, _ in app.writing_items.values()] == [
        "Personal messages: Casual", "Work messages: Formal", "Email: Formal",
        "AI apps and terminals: Casual", "Other apps: Formal"]
    sub, _, items = app.writing_items["personal"]
    app.set_writing_style(items["very_casual"])
    assert app.saved[-1]["app_styles"]["personal"] == "very_casual"
    assert sub.title == "Personal messages: Very casual"
    assert [k for k, i in items.items() if i.state] == ["very_casual"]


def test_styles_can_be_paused(menu_app):
    app = menu_app
    app._build_writing_menu()
    assert app.writing_menu.title == "Writing style per app" and not app.pause_styles_item.state
    app.toggle_styles_paused()
    assert app.saved[-1]["styles_paused"] is True and app.pause_styles_item.state
    assert app.writing_menu.title == "Writing style per app: paused"
    assert app.saved[-1]["app_styles"] == DEFAULT_CONFIG["app_styles"]   # your choices are kept
    app.toggle_styles_paused()
    assert app.saved[-1]["styles_paused"] is False and app.writing_menu.title == "Writing style per app"


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


# ---- Foni's own keystrokes (Cmd+V to type) must not cancel a dictation you've just started
def test_own_paste_does_not_cancel_the_next_dictation(keys):
    from Quartz import CGEventSetIntegerValueField, kCGEventSourceUserData
    from foni.system import OWN_EVENT
    app, ev, drain = keys
    ev("mod", 63, FN)                          # you start the next dictation...
    assert drain() == ["press"]
    e = CGEventCreateKeyboardEvent(None, 9, True)   # ...while Foni types the last one (Cmd+V)
    CGEventSetFlags(e, CMD)
    CGEventSetIntegerValueField(e, kCGEventSourceUserData, OWN_EVENT)
    app._on_event(None, kCGEventKeyDown, e, None)
    assert drain() == [] and app.keys_down == {"fn"}
    ev("down", 9, CMD)                         # but your own Cmd+V still counts as a shortcut
    assert drain() == ["chord"]


# ---- typing: always type; add the Copy button when macOS can't confirm a text box
@pytest.mark.parametrize("target, copy", [(True, False), (None, True), (False, True)])
def test_always_types(target, copy, tmp_path, monkeypatch):
    import foni.app as appmod
    import numpy as np
    typed = []
    monkeypatch.setattr(appmod, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(appmod, "focused_text_target", lambda: (target, "AXWebArea"))
    monkeypatch.setattr(appmod, "type_text", lambda text, saved=None: typed.append(text))
    monkeypatch.setattr(appmod, "wait_for_modifiers_released", lambda: None)
    monkeypatch.setattr(appmod, "has_speech", lambda audio: True)
    app = types.SimpleNamespace(
        cfg=json.loads(json.dumps(DEFAULT_CONFIG)) | {"ai_editing": "off"}, pending_copy=None,
        transcriber=types.SimpleNamespace(transcribe=lambda audio: "see you at five"),
        dictionary=types.SimpleNamespace(hints_for=lambda text: [], entries={}), watch_token=0,
        stats=types.SimpleNamespace(add=lambda rec: None), last_text="", _get_ai=lambda: None,
        _watch_edits=lambda text, token: None)
    app._style_setting = Foni._style_setting.__get__(app)
    Foni._handle(app, np.zeros(16000, dtype=np.float32), False, "Claude")
    assert typed == ["See you at five "]
    assert (app.pending_copy == "See you at five") is copy


@pytest.mark.parametrize("paused, typed_text", [(False, "see you at five "), (True, "See you at five. ")])
def test_paused_styles_type_it_as_you_said_it(paused, typed_text, tmp_path, monkeypatch):
    import foni.app as appmod
    import numpy as np
    typed = []
    monkeypatch.setattr(appmod, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(appmod, "focused_text_target", lambda: (True, "AXTextArea"))
    monkeypatch.setattr(appmod, "type_text", lambda text, saved=None: typed.append(text))
    monkeypatch.setattr(appmod, "has_speech", lambda audio: True)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG)) | {"ai_editing": "off", "styles_paused": paused}
    cfg["app_styles"]["personal"] = "very_casual"
    app = types.SimpleNamespace(
        cfg=cfg, pending_copy=None,
        transcriber=types.SimpleNamespace(transcribe=lambda audio: "See you at five."),
        dictionary=types.SimpleNamespace(hints_for=lambda text: [], entries={}), watch_token=0,
        stats=types.SimpleNamespace(add=lambda rec: None), last_text="", _get_ai=lambda: None,
        _watch_edits=lambda text, token: None)
    app._style_setting = Foni._style_setting.__get__(app)
    Foni._handle(app, np.zeros(16000, dtype=np.float32), False, "WhatsApp")
    assert typed == [typed_text]   # very casual in WhatsApp, unless the styles are paused


# ---- updates: one click, and clicking again while it installs does nothing
def test_update_is_one_click(monkeypatch):
    started = []
    monkeypatch.setattr("foni.app.threading.Thread",
                        lambda target, **kw: types.SimpleNamespace(start=lambda: started.append(target)))
    app = types.SimpleNamespace(updating=False, update=("9.9.9", "", "url", "page"),
                                update_checked=0, pending_toast=None,
                                _install_update=lambda: None, _check_updates=lambda manual: None)
    for m in ("update_now", "install_update"):
        setattr(app, m, getattr(Foni, m).__get__(app))
    for _ in range(5):
        app.install_update()
        app.update_now()
    assert len(started) == 1 and app.updating
    assert app.pending_toast == "Already updating…"


# ---- settings from an older Foni get styles for kinds of app added since
def test_new_style_groups_get_their_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"app_styles": {"personal": "very_casual"}}))
    styles = config.load_config()["app_styles"]
    assert styles["personal"] == "very_casual" and styles["ai"] == "casual"


# ---- speech: dictations over 2 minutes (they used to need FFmpeg, and were lost without it)
def test_long_dictation_is_cut_at_pauses():
    import numpy as np
    from foni.speech import split_at_pauses
    rng = np.random.default_rng(0)
    audio = rng.normal(0, 0.1, 16000 * 200).astype(np.float32)   # 200 s of talking...
    audio[16000 * 70:16000 * 71] = 0                              # ...a pause at 70 s
    audio[16000 * 150:16000 * 151] = 0                            # ...and one at 150 s
    pieces = split_at_pauses(audio)
    assert sum(len(p) for p in pieces) == len(audio)              # nothing lost or doubled
    assert [round(len(p) / 16000) for p in pieces] == [70, 80, 50]


def test_long_dictation_transcribes_every_piece(monkeypatch):
    import numpy as np
    from foni.speech import Transcriber
    t = Transcriber.__new__(Transcriber)
    t.engine = "parakeet"
    heard = []
    t._parakeet_piece = lambda audio: heard.append(len(audio)) or f"part {len(heard)}."
    text = t.transcribe(np.zeros(16000 * 200, dtype=np.float32))
    assert len(heard) > 2 and text == " ".join(f"part {i}." for i in range(1, len(heard) + 1))
    assert sum(heard) == 16000 * 200 + 2 * int(16000 * 0.15)     # every bit of it, once


def test_update_check_soon_after_start_then_every_6_hours():
    from foni.app import UPDATE_EVERY
    start = 1_000_000.0
    app = types.SimpleNamespace(cfg={"check_updates": True}, update_checked=start - UPDATE_EVERY + 10)
    due = Foni._update_check_due.__get__(app)
    assert not due(start + 5) and due(start + 11)             # 10 seconds after Foni starts
    assert not due(start + 11 + 5 * 3600) and due(start + 11 + 6 * 3600 + 1)   # then every 6 hours
    app.cfg["check_updates"] = False
    assert not due(start + 99 * 3600)                        # never, if you turned it off


def test_update_menu_says_where_things_stand():
    from foni import __version__
    app = types.SimpleNamespace(update=None, updating=False, update_step=None, checking=False,
                                up_to_date=False, install_update="install", update_now="check")
    menu = Foni._update_menu.__get__(app)
    assert menu() == ((None, None), ("Check for updates", "check"))   # not asked GitHub yet
    app.checking = True
    assert menu() == ((None, None), ("Checking for updates…", None))  # greyed out
    app.checking, app.up_to_date = False, True
    # after updating: no update button left, just where you stand
    assert menu() == ((None, None), (f"✓ Foni {__version__} is up to date", "check"))
    app.update, app.up_to_date = ("9.9.9", "", "url", "page"), False
    assert menu() == (("⬆︎ Update to Foni 9.9.9", "install"), (None, None))  # the one button
    app.updating, app.update_step = True, "downloading 42%"
    assert menu() == (("Updating to Foni 9.9.9… downloading 42%", None), (None, None))
    app.update_step = "installing"
    assert menu()[0] == ("Updating to Foni 9.9.9… installing", None)


def test_update_steps_show_in_the_menu_bar():
    app = types.SimpleNamespace(update_step=None, setup_status=None)
    step = Foni._update_step.__get__(app)
    for said, bar in (("downloading 42%", "↓ 42%"), ("installing", "Installing…"),
                      ("restarting", "Restarting…")):
        step(said)
        assert app.update_step == said and app.setup_status == bar
