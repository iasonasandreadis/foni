"""The setup window: a live checklist of what Foni needs, with a button for each step.

It opens on first launch and whenever a permission is missing (menu → Setup & permissions…).
Each line turns into a green tick as soon as you allow it in System Settings."""
import platform
import subprocess

import objc
from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSImageView,
    NSMakeRect,
    NSObject,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Quartz import (
    CGPreflightListenEventAccess,
    CGPreflightPostEventAccess,
    CGRequestListenEventAccess,
    CGRequestPostEventAccess,
)

SETTINGS = "x-apple.systempreferences:com.apple.preference.security?"
KEYBOARD = "x-apple.systempreferences:com.apple.Keyboard-Settings.extension"
W, H = 540, 610
# macOS 27 renamed the Accessibility permission page.
ACCESS = ("Device Control and Data Access"
          if int((platform.mac_ver()[0] or "0").split(".")[0] or 0) >= 27 else "Accessibility")


def mic_status():
    """'ok', 'ask' (never asked) or 'denied'."""
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        s = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        return {3: "ok", 0: "ask"}.get(s, "denied")
    except Exception:
        return "ask"


def request_mic(done=None):
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, lambda granted: done and done(granted))
    except Exception as e:
        print(f"[setup] microphone request failed: {e}")


def permissions():
    """{"mic": bool, "listen": bool, "post": bool} — what Foni has right now."""
    return {"mic": mic_status() == "ok", "listen": bool(CGPreflightListenEventAccess()),
            "post": bool(CGPreflightPostEventAccess())}


def _label(text, x, y, w, h, size=13, bold=False, color=None):
    f = NSTextField.labelWithString_(text)
    f.setFrame_(NSMakeRect(x, y, w, h))
    f.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    if color is not None:
        f.setTextColor_(color)
    f.setLineBreakMode_(0)  # wrap
    f.cell().setWraps_(True)
    return f


class SetupController(NSObject):
    """Receives the window's button clicks (AppKit needs an Objective-C object for that)."""

    def initWithOwner_(self, owner):
        self = objc.super(SetupController, self).init()
        if self is not None:
            self.owner = owner
        return self

    def allowMic_(self, sender):
        if mic_status() == "denied":
            subprocess.run(["open", SETTINGS + "Privacy_Microphone"])
        else:
            request_mic()

    def openListen_(self, sender):
        CGRequestListenEventAccess()  # adds Foni to the list, so there's a switch to turn on
        subprocess.run(["open", SETTINGS + "Privacy_ListenEvent"])

    def openAccess_(self, sender):
        CGRequestPostEventAccess()
        subprocess.run(["open", SETTINGS + "Privacy_Accessibility"])

    def openKeyboard_(self, sender):
        subprocess.run(["open", KEYBOARD])

    def reset_(self, sender):
        self.owner.reset_permissions()

    def done_(self, sender):
        self.owner.close()


class SetupWindow:
    STEPS = (  # (key, title, why, button title, action)
        ("mic", "Microphone", "So Foni can hear you while you hold the key.", "Allow", "allowMic:"),
        ("listen", "Input Monitoring", "So Foni notices when you hold the key.", "Open Settings", "openListen:"),
        ("post", ACCESS, "So Foni can type the text for you.", "Open Settings", "openAccess:"),
        ("model", "Speech model", "Downloads once (about 2.5 GB), then works offline.", None, None),
    )

    def __init__(self, app):
        self.app = app
        self.ctrl = SetupController.alloc().initWithOwner_(self)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        self.window.setTitle_("Set up Foni")
        self.window.setReleasedWhenClosed_(False)
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        self.window.setContentView_(view)
        muted = NSColor.secondaryLabelColor()

        hot = app.hotkey_name()
        view.addSubview_(_label("Welcome to Foni", 32, H - 66, W - 64, 30, 22, bold=True))
        view.addSubview_(_label(f"Hold {hot}, talk, and let go: your words appear where you're typing. "
                                "Allow these and you're set.", 32, H - 112, W - 64, 40, 13, color=muted))
        self.rows = {}
        y = H - 178
        for key, title, why, button, action in self.STEPS:
            icon = NSImageView.alloc().initWithFrame_(NSMakeRect(32, y + 14, 22, 22))
            view.addSubview_(icon)
            view.addSubview_(_label(title, 66, y + 22, 280, 18, 14, bold=True))
            detail = _label(why, 66, y, 300, 18, 12, color=muted)
            view.addSubview_(detail)
            btn = None
            if button:
                btn = NSButton.alloc().initWithFrame_(NSMakeRect(W - 32 - 130, y + 8, 130, 30))
                btn.setTitle_(button)
                btn.setBezelStyle_(NSBezelStyleRounded)
                btn.setTarget_(self.ctrl)
                btn.setAction_(action)
                view.addSubview_(btn)
            self.rows[key] = (icon, detail, btn, why)
            y -= 62

        tip_y = y - 12
        view.addSubview_(_label("If Fn also opens the emoji picker: System Settings → Keyboard → "
                                "“Press 🌐 key to” → “Do Nothing”.", 32, tip_y, W - 64 - 150, 34, 12, color=muted))
        kb = NSButton.alloc().initWithFrame_(NSMakeRect(W - 32 - 130, tip_y + 2, 130, 30))
        kb.setTitle_("Keyboard Settings")
        kb.setBezelStyle_(NSBezelStyleRounded)
        kb.setTarget_(self.ctrl)
        kb.setAction_("openKeyboard:")
        view.addSubview_(kb)

        try_y = tip_y - 66
        view.addSubview_(_label("Try it", 32, try_y + 34, 200, 18, 14, bold=True))
        self.try_field = NSTextField.alloc().initWithFrame_(NSMakeRect(32, try_y, W - 64, 28))
        self.try_field.setPlaceholderString_(f"Click here, hold {hot} and say something")
        view.addSubview_(self.try_field)

        self.reset_btn = NSButton.alloc().initWithFrame_(NSMakeRect(26, 22, 200, 30))
        self.reset_btn.setTitle_("Reset permissions…")
        self.reset_btn.setBezelStyle_(NSBezelStyleRounded)
        self.reset_btn.setTarget_(self.ctrl)
        self.reset_btn.setAction_("reset:")
        view.addSubview_(self.reset_btn)
        self.hint = _label("A switch is on but this still shows ✗? Reset, then turn it on again.",
                           32, 56, W - 64, 18, 11, color=muted)
        view.addSubview_(self.hint)
        done = NSButton.alloc().initWithFrame_(NSMakeRect(W - 32 - 100, 22, 100, 30))
        done.setTitle_("Done")
        done.setBezelStyle_(NSBezelStyleRounded)
        done.setKeyEquivalent_("\r")
        done.setTarget_(self.ctrl)
        done.setAction_("done:")
        view.addSubview_(done)
        self.done_btn = done
        self.window.center()
        self.refresh()

    # ---- state
    def _set(self, key, ok, detail=None, pending=False):
        icon, label, btn, why = self.rows[key]
        name = "checkmark.circle.fill" if ok else ("arrow.down.circle" if pending else "circle")
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        icon.setImage_(img)
        icon.setContentTintColor_(NSColor.systemGreenColor() if ok else NSColor.tertiaryLabelColor())
        label.setStringValue_(detail or why)
        if btn is not None:
            btn.setHidden_(ok)

    def refresh(self, perms=None):
        p = perms or permissions()
        self._set("mic", p["mic"], None if p["mic"] or mic_status() != "denied"
                  else "Turned off. Click Allow to open the setting and turn it on.")
        self._set("listen", p["listen"])
        self._set("post", p["post"])
        status = self.app.setup_status
        if self.app.state == "loading":
            self._set("model", False, f"Downloading… {status.split()[-1]}" if status else
                      "Loading…", pending=True)
        elif status and status.startswith("⚠"):
            self._set("model", False, "Couldn't download it. Check your internet, then Restart Foni.")
        else:
            self._set("model", True, "Ready. It runs on your Mac, offline.")
        ready = all(p.values()) and self.app.state != "loading"
        self.done_btn.setTitle_("Start using Foni" if ready else "Done")
        self.done_btn.setFrame_(NSMakeRect(W - 32 - (150 if ready else 100), 22, 150 if ready else 100, 30))
        return p

    # ---- showing
    @property
    def visible(self):
        return bool(self.window.isVisible())

    def show(self):
        self.refresh()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def close(self):
        self.window.orderOut_(None)

    def reset_permissions(self):
        self.app.reset_permissions()
