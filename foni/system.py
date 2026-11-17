import time
from AppKit import (
    NSPasteboard,
    NSPasteboardItem,
    NSPasteboardTypeString,
    NSSound,
    NSWorkspace,
)
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementCreateSystemWide,
    AXUIElementIsAttributeSettable,
    AXUIElementSetAttributeValue,
)
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventGetIntegerValueField,
    CGEventPost,
    CGEventSetFlags,
    CGEventSetIntegerValueField,
    CGEventSourceFlagsState,
    kCGEventFlagMaskCommand,
    kCGEventSourceStateHIDSystemState,
    kCGEventSourceUserData,
    kCGHIDEventTap,
)
from .hotkeys import CMD, CTRL, FN, OPT, SHIFT


def play(name, cfg):
    if cfg["sounds"]:
        s = NSSound.soundNamed_(name)
        if s:
            s.play()


def frontmost_app():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(app.localizedName()) if app else ""


def focused_element():
    err, el = AXUIElementCopyAttributeValue(AXUIElementCreateSystemWide(), "AXFocusedUIElement", None)
    return el if err == 0 else None


def element_text(el):
    err, v = AXUIElementCopyAttributeValue(el, "AXValue", None)
    return str(v) if err == 0 and isinstance(v, str) else None


# ---------------------------------------------------------------- clipboard & keystrokes

def wait_for_modifiers_released(timeout=0.6):
    end = time.time() + timeout
    while time.time() < end and CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState) & (
            FN | OPT | CMD | CTRL | SHIFT):
        time.sleep(0.015)


# Marks the keystrokes Foni sends itself (Cmd+V to type, Cmd+C to read a selection), so its own
# keyboard watcher doesn't take them for you pressing a key, which would cancel a dictation
# you'd already started while the last one was still being typed.
OWN_EVENT = 0x466F6E69  # "Foni"
_last_sent = 0.0


def press_cmd(keycode):
    global _last_sent
    _last_sent = time.time()
    for down in (True, False):
        ev = CGEventCreateKeyboardEvent(None, keycode, down)
        CGEventSetFlags(ev, kCGEventFlagMaskCommand)
        CGEventSetIntegerValueField(ev, kCGEventSourceUserData, OWN_EVENT)
        CGEventPost(kCGHIDEventTap, ev)


def is_own_event(event, keycode=None):
    if CGEventGetIntegerValueField(event, kCGEventSourceUserData) == OWN_EVENT:
        return True
    # in case the mark gets lost on the way: Cmd+V / Cmd+C that Foni sent a moment ago
    return keycode in (8, 9) and sent_recently(0.3)


def sent_recently(seconds=0.6):
    return time.time() - _last_sent < seconds


def snapshot_clipboard():
    pb = NSPasteboard.generalPasteboard()
    items = []
    for item in pb.pasteboardItems() or []:
        items.append({t: item.dataForType_(t) for t in item.types()})
    return items


def restore_clipboard(items):
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    objs = []
    for d in items:
        it = NSPasteboardItem.alloc().init()
        for t, data in d.items():
            if data is not None:
                it.setData_forType_(data, t)
        objs.append(it)
    if objs:
        pb.writeObjects_(objs)


def grab_selection():
    """Copy the current selection (Cmd+C). Returns (selected text or None, clipboard snapshot)."""
    pb = NSPasteboard.generalPasteboard()
    saved = snapshot_clipboard()
    before = pb.changeCount()
    press_cmd(8)  # 'c'
    end = time.time() + 0.3
    while pb.changeCount() == before and time.time() < end:
        time.sleep(0.01)
    selected = pb.stringForType_(NSPasteboardTypeString) if pb.changeCount() != before else None
    return (str(selected) if selected else None), saved


def type_text(text, saved=None):
    """Paste text into the focused app, then put the user's clipboard back."""
    pb = NSPasteboard.generalPasteboard()
    if saved is None:
        saved = snapshot_clipboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)
    time.sleep(0.02)
    press_cmd(9)  # 'v'
    time.sleep(0.4)
    restore_clipboard(saved)


# ---------------------------------------------------------------- where will the text go?

TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}
NON_TEXT_ROLES = {
    "AXWindow", "AXApplication", "AXButton", "AXList", "AXOutline", "AXTable", "AXImage",
    "AXStaticText", "AXMenuBar", "AXMenu", "AXMenuItem", "AXRow", "AXToolbar", "AXCheckBox",
    "AXRadioButton", "AXPopUpButton", "AXSlider", "AXTabGroup", "AXBrowser", "AXSplitGroup",
    "AXLink", "AXDockItem",
}
# Plain page/content areas: focused when you click on a page that isn't a text box
PAGE_ROLES = {"AXGroup", "AXWebArea", "AXScrollArea", "AXLayoutArea", "AXSplitter"}
AX_NO_VALUE = -25212


def focused_text_target():
    """True: a text box has focus. False: it looks like there's nothing to type into. None:
    can't tell. Apps built on web pages (Claude, Slack, Electron apps) sometimes answer a
    moment late, so an unsure answer is checked once more."""
    target, why = _focused_text_target()
    if target is not True:
        time.sleep(0.1)
        target, why = _focused_text_target()
    return target, why


def _focused_text_target():
    try:
        err, el = AXUIElementCopyAttributeValue(AXUIElementCreateSystemWide(),
                                                "AXFocusedUIElement", None)
        if err == AX_NO_VALUE or el is None:
            # Chromium/Electron apps (Slack, Discord, VS Code, ...) only expose their UI
            # to accessibility once asked to.
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app:
                ax_app = AXUIElementCreateApplication(app.processIdentifier())
                AXUIElementSetAttributeValue(ax_app, "AXManualAccessibility", True)
                err, el = AXUIElementCopyAttributeValue(ax_app, "AXFocusedUIElement", None)
        if err != 0 or el is None:
            # The app won't say what has focus (some apps never do): we can't tell, so type the
            # text anyway and offer Copy too, rather than dropping it into a Copy button only.
            return None, f"ax error {err}"
        role = AXUIElementCopyAttributeValue(el, "AXRole", None)[1]
        if role in TEXT_ROLES:
            return True, role
        for attr in ("AXSelectedTextRange", "AXValue"):
            e, settable = AXUIElementIsAttributeSettable(el, attr, None)
            if e == 0 and settable:
                return True, f"{role} (editable)"
        # Web pages / Electron apps: text inside an editable region has an editable ancestor
        e, anc = AXUIElementCopyAttributeValue(el, "AXEditableAncestor", None)
        if e == 0 and anc is not None:
            return True, f"{role} (inside editable)"
        if role in NON_TEXT_ROLES or role in PAGE_ROLES:
            return False, role
        return None, role
    except Exception as e:
        return None, f"check failed: {e}"
