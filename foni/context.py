"""Reading the conversation: the text around your cursor (the chat above the box you're typing
in, the document you're writing), read when you start a dictation. A cloud AI uses it to fix
words that don't fit the conversation ("an AI riding them" → "writing") and to spell names the
way they're written on screen; the speech model listens out for those names too.

It's read through macOS Accessibility, the permission Foni already has for typing. Never from
password fields or password managers, anything that looks like a key or password is blanked
out, and nothing is kept once the dictation is typed. Menu → AI clean-up → Read the
conversation around your cursor turns it off."""
import re
import time

from .learning import COMMON

MAX_CHARS = 1500      # of the conversation above the box
SHORT, MAX_SHORT = 25, 300  # bits of text shorter than SHORT: at most MAX_SHORT characters
BOX_BEFORE = 600      # of what's already in the box, before the cursor
BOX_AFTER = 300
BUDGET = 0.3          # seconds; a dictation never waits longer than this for it
MAX_NODES = 2500

# Never read: password managers and the like
SKIP_APPS = {"1Password", "1Password 7", "Bitwarden", "Dashlane", "LastPass", "KeePassXC",
             "Keychain Access", "Passwords", "Enpass", "Proton Pass"}
SECURE = {"AXSecureTextField"}
TEXT_ROLES = {"AXStaticText", "AXTextArea", "AXTextField", "AXHeading"}
BOX_ROLES = {"AXTextArea", "AXTextField", "AXComboBox", "AXSearchField"}
# Buttons, menus, tabs: their labels ("Chat mode", "Send") aren't the conversation
CONTROLS = {"AXButton", "AXPopUpButton", "AXMenuButton", "AXCheckBox", "AXRadioButton",
            "AXTabGroup", "AXToolbar", "AXMenuBar", "AXMenu", "AXMenuItem", "AXComboBox",
            "AXSlider", "AXIncrementor", "AXDisclosureTriangle", "AXImage", "AXSearchField",
            "AXProgressIndicator", "AXBusyIndicator"}
# API keys, tokens, long random strings: never sent anywhere
SECRET_RE = re.compile(
    r"\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|xox[abprs]|AKIA|AIza)[-_A-Za-z0-9]{12,}"
    r"|\b(?=[A-Za-z0-9+/_=-]*\d)(?=[A-Za-z0-9+/_=-]*[A-Za-z])[A-Za-z0-9+/_=-]{32,}")


def hide_secrets(text):
    return SECRET_RE.sub("[hidden]", text)


class MacAX:
    """The few Accessibility calls the reader needs (tests use a pretend version)."""

    def __init__(self, timeout=0.15, pid=None):
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCopyMultipleAttributeValues,
            AXUIElementCreateApplication,
            AXUIElementCreateSystemWide,
            AXUIElementGetTypeID,
            AXUIElementSetAttributeValue,
            AXUIElementSetMessagingTimeout,
            AXValueGetValue,
            kAXValueCFRangeType,
        )
        from AppKit import NSRunningApplication, NSWorkspace
        from CoreFoundation import CFEqual, CFGetTypeID
        self.is_element = lambda v: v is not None and CFGetTypeID(v) == AXUIElementGetTypeID()
        self.copy, self.copy_many = AXUIElementCopyAttributeValue, AXUIElementCopyMultipleAttributeValues
        self.set, self.set_timeout = AXUIElementSetAttributeValue, AXUIElementSetMessagingTimeout
        self.value_of, self.range_type, self.cf_equal = AXValueGetValue, kAXValueCFRangeType, CFEqual
        self.timeout = timeout
        # normally the app in front; `pid`: a given app, even in the background (for testing)
        self.pid_given = pid is not None
        app = (NSRunningApplication.runningApplicationWithProcessIdentifier_(pid) if pid
               else NSWorkspace.sharedWorkspace().frontmostApplication())
        self.app_name = str(app.localizedName()) if app else ""
        self.app = AXUIElementCreateApplication(app.processIdentifier()) if app else None
        self.system = AXUIElementCreateSystemWide()
        for el in (self.app, self.system):
            if el is not None:
                self.set_timeout(el, timeout)

    def focused(self):
        err, el = self.copy(self.app if self.pid_given else self.system, "AXFocusedUIElement", None)
        if (err != 0 or el is None) and self.app is not None:
            # Chromium/Electron apps (Claude, Slack, Discord, ...) only show their contents to
            # Accessibility once asked to
            self.set(self.app, "AXManualAccessibility", True)
            err, el = self.copy(self.app, "AXFocusedUIElement", None)
        return el if err == 0 else None

    def get(self, el, names):
        """{name: value} for the attributes that have one."""
        try:
            err, values = self.copy_many(el, names, 0, None)
        except Exception:
            return {}
        if err != 0 or values is None:
            return {}
        out = {}
        for name, v in zip(names, values):
            if isinstance(v, str):
                out[name] = v
            elif name == "AXChildren" and v is not None and hasattr(v, "__len__"):
                out[name] = [k for k in v if self.is_element(k)]
            elif name in ("AXParent", "AXEditableAncestor") and self.is_element(v):
                out[name] = v
        return out

    def cursor(self, el):
        """(start, length) of the selection / cursor in a text box, or None."""
        err, v = self.copy(el, "AXSelectedTextRange", None)
        if err != 0 or v is None:
            return None
        ok, r = self.value_of(v, self.range_type, None)
        if not ok or r is None:
            return None
        return (r.location, r.length) if hasattr(r, "location") else (int(r[0]), int(r[1]))

    def same(self, a, b):
        return bool(self.cf_equal(a, b))


def read(ax=None, budget=BUDGET, element=None):
    """{"app", "screen", "seconds"} for the text box that has focus (or `element`), or None
    when there's nothing to read (or it mustn't be read)."""
    t = time.monotonic()
    deadline = t + budget
    try:
        ax = ax or MacAX()
        if ax.app_name in SKIP_APPS:
            return None
        el = element if element is not None else ax.focused()
        if el is None:
            return None
        me = ax.get(el, ["AXRole", "AXSubrole", "AXValue"])
        if me.get("AXRole") in SECURE or me.get("AXSubrole") in SECURE:
            return None
        if me.get("AXRole") not in BOX_ROLES and not ax.get(el, ["AXEditableAncestor"]):
            return None  # not typing into a text box (a button, a page): nothing to go on
        value = me.get("AXValue") or ""
        cur = ax.cursor(el) if value else None
        if cur is not None:
            before, after = value[:cur[0]], value[cur[0] + cur[1]:]
        else:
            before, after = value, ""
        before, after = before[-BOX_BEFORE:], after[:BOX_AFTER]
        nearby = _nearby(ax, el, deadline)
        box = f"{before}[cursor]{after}" if (before.strip() or after.strip()) else ""
        parts = [p for p in (nearby, f"--- the text box (the dictation goes at [cursor]) ---\n{box}"
                             if box else "") if p]
        screen = hide_secrets("\n".join(parts)).strip()
        return {"app": ax.app_name, "screen": screen, "seconds": time.monotonic() - t}
    except Exception as e:
        print(f"[context] couldn't read the screen: {type(e).__name__}")
        return None


def _nearby(ax, focused, deadline):
    """Text just above / before the focused box, nearest first, until MAX_CHARS: walk up from
    the box and read the earlier siblings at each level (the conversation above a chat box)."""
    parts, total, short_total, nodes = [], 0, 0, [0]
    node = focused
    for _ in range(60):  # web apps nest the box 20-40 levels deep
        parent = ax.get(node, ["AXParent"]).get("AXParent")
        if parent is None:
            break
        info = ax.get(parent, ["AXRole", "AXChildren"])
        role = info.get("AXRole")
        if role == "AXApplication":
            break
        kids = info.get("AXChildren") or []
        idx = next((i for i, k in enumerate(kids) if ax.same(k, node)), len(kids))
        for sib in reversed(kids[:idx]):
            for text in _texts_backwards(ax, sib, deadline, nodes):
                if len(text) < SHORT:  # labels, times, "#1": keep a few, not a screenful
                    if len(text) < 3 or short_total >= MAX_SHORT:
                        continue
                    short_total += len(text) + 1
                parts.append(text)
                total += len(text) + 1
                if total >= MAX_CHARS:
                    return _join(parts)
            if time.monotonic() > deadline or nodes[0] > MAX_NODES:
                return _join(parts)
        if role in ("AXWindow", "AXWebArea"):  # the page ends here; beyond it is the app's frame
            break
        node = parent
    return _join(parts)


def _join(parts_nearest_first):
    text = "\n".join(reversed(parts_nearest_first))
    return text[-MAX_CHARS:]


def _texts_backwards(ax, el, deadline, nodes):
    """The text inside `el`, last piece first."""
    stack = [el]
    while stack:
        if time.monotonic() > deadline or nodes[0] > MAX_NODES:
            return
        nodes[0] += 1
        cur = stack.pop()
        info = ax.get(cur, ["AXRole", "AXSubrole", "AXValue", "AXChildren"])
        role = info.get("AXRole")
        if role in SECURE or info.get("AXSubrole") in SECURE or role in CONTROLS:
            continue
        kids = info.get("AXChildren") or []
        if role in TEXT_ROLES and not kids:
            text = (info.get("AXValue") or "").strip()
            if text:
                yield text
        elif role in ("AXTextArea", "AXTextField") and info.get("AXValue"):
            yield info["AXValue"].strip()      # a whole text area: don't read it twice
            continue
        stack.extend(kids)                     # popped last-first: reading backwards


# ---------------------------------------------------------------- names on screen

WORD_RE = re.compile(r"[^\W\d_][\w'’.-]*[^\W_]|[^\W\d_]")


def names_in(text, known=(), limit=15):
    """Names and brand words on screen ("Aoife", "TikTok", "iCloud") for the speech model to
    listen out for: capitalized mid-sentence or mixed-case, 4+ letters, not everyday words."""
    known_l = {k.lower() for k in known}
    seen = {}
    for m in WORD_RE.finditer(text or ""):
        w = m.group(0).strip(".'’-")
        if len(w) < 4 or w.lower() in COMMON or w.lower() in known_l or "[" in w:
            continue
        mixed = any(c.isupper() for c in w[1:]) and any(c.islower() for c in w)
        start = text[:m.start()].rstrip(" \t")
        sentence_start = not start or start[-1] in ".!?:\n\"“-–—]"
        speaker = text[m.end():m.end() + 1] == ":"  # "Aoife: see you there" in a chat
        if mixed or (w[0].isupper() and not w.isupper() and (speaker or not sentence_start)):
            seen[w] = seen.get(w, 0) + 1
    return sorted(seen, key=lambda w: -seen[w])[:limit]
