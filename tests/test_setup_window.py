"""The setup window shows a live tick per step, and hides each step's button once it's done."""
import types

from foni.setup_window import SetupWindow


def _app(state="idle", status=None):
    return types.SimpleNamespace(state=state, setup_status=status, hotkey_name=lambda: "Fn",
                                 reset_permissions=lambda: None)


def test_missing_permissions_show_buttons():
    w = SetupWindow(_app())
    w.refresh({"mic": False, "listen": False, "post": True})
    assert not w.rows["mic"][2].isHidden() and not w.rows["listen"][2].isHidden()
    assert w.rows["post"][2].isHidden()                     # done: no button
    assert w.done_btn.title() == "Done"


def test_all_done():
    w = SetupWindow(_app())
    w.refresh({"mic": True, "listen": True, "post": True})
    assert w.done_btn.title() == "Start using Foni"
    assert w.rows["model"][1].stringValue().startswith("Ready")


def test_download_progress():
    w = SetupWindow(_app(state="loading", status="↓ speech model 42%"))
    w.refresh({"mic": True, "listen": True, "post": True})
    assert w.rows["model"][1].stringValue() == "Downloading… 42%"
    assert w.done_btn.title() == "Done"
