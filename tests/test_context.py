"""Reading the conversation around your cursor, and what Claude does with it. A pretend
Accessibility tree stands in for a real app (the real Claude app and a test chat page were
checked by hand: the messages above the box were read in 0.02 s)."""
import types

from foni import context
from foni.ai_local import AIEditor
from foni.config import DEFAULT_CONFIG
from foni.text import faithful_edit, postprocess


class Node(dict):
    def __init__(self, role, value=None, kids=(), subrole=None):
        super().__init__(AXRole=role, AXSubrole=subrole, AXValue=value, AXChildren=list(kids))
        for k in kids:
            k["AXParent"] = self
        self["AXParent"] = None

    __hash__ = object.__hash__
    __eq__ = object.__eq__


class FakeAX:
    def __init__(self, root, focus, app="Claude", cursor=None):
        self.root, self.focus, self.app_name, self._cursor = root, focus, app, cursor

    def focused(self):
        return self.focus

    def get(self, el, names):
        return {n: el.get(n) for n in names if el.get(n) not in (None, [])}

    def cursor(self, el):
        return self._cursor

    def same(self, a, b):
        return a is b


def chat(box_role="AXTextArea", typed="Thanks, and "):
    box = Node(box_role, typed)
    messages = Node("AXGroup", kids=[
        Node("AXGroup", kids=[Node("AXStaticText", "Assistant:"),
                              Node("AXStaticText", "I can leave suggestions on every script.")]),
        Node("AXGroup", kids=[Node("AXStaticText", "You:"),
                              Node("AXStaticText", "I'm supposed to be writing the suggestions, "
                                                   "not an AI writing them.")]),
        Node("AXGroup", kids=[Node("AXStaticText", "Siobhán can help. Your key "
                                                   "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx stays here.")]),
    ])
    toolbar = Node("AXGroup", kids=[Node("AXButton", kids=[Node("AXStaticText", "Chat mode")])])
    page = Node("AXWebArea", kids=[Node("AXButton", kids=[Node("AXStaticText", "New chat")]),
                                   messages, Node("AXGroup", kids=[toolbar, box])])
    Node("AXWindow", kids=[Node("AXGroup", kids=[Node("AXStaticText", "Sidebar: Old chat")]), page])
    return box


def test_reads_the_conversation_above_the_box_and_where_the_cursor_is():
    box = chat()
    got = context.read(FakeAX(None, box, cursor=(12, 0)))
    screen = got["screen"]
    assert screen.index("I can leave") < screen.index("not an AI writing") < screen.index("Siobhán")
    assert screen.endswith("Thanks, and [cursor]")
    assert "sk-ant" not in screen and "[hidden]" in screen          # keys never leave the Mac
    assert "Chat mode" not in screen and "New chat" not in screen   # button labels aren't the chat
    assert "Sidebar" not in screen                                  # outside the page


def test_never_reads_password_fields_password_managers_or_non_text_boxes():
    assert context.read(FakeAX(None, chat(box_role="AXTextField")).__class__(
        None, Node("AXTextField", "hunter2", subrole="AXSecureTextField"))) is None
    assert context.read(FakeAX(None, chat(), app="1Password")) is None
    assert context.read(FakeAX(None, Node("AXButton"))) is None


def test_names_on_screen_for_the_speech_model():
    text = "Shopping list for Friday\nMilk, eggs, and the TikTok ring light for Aoife. iCloud is full."
    assert context.names_in(text) == ["Friday", "TikTok", "Aoife", "iCloud"]
    assert context.names_in(text, known=("TikTok",)) == ["Friday", "Aoife", "iCloud"]


class FakeEditor:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def clean(self, text, style, vocab, corrections=(), screen=None):
        self.calls.append(screen)
        return self.reply


CLOUD = dict(DEFAULT_CONFIG, ai_editing="smart", ai_engine="cloud")
LOCAL = dict(DEFAULT_CONFIG, ai_editing="smart", ai_engine="local")
SCREEN = "You: I'm supposed to be writing the suggestions on the scripts"


def test_claude_checks_every_dictation_the_mac_model_only_messy_ones():
    ed = FakeEditor("I meant not an AI writing them.")
    assert postprocess("I meant not an AI riding them.", CLOUD, ed, "Claude", screen=SCREEN) == \
        ("I meant not an AI writing them.", True)
    assert ed.calls == [SCREEN]                                   # Claude saw the conversation
    ed = FakeEditor("Sounds good.")
    assert postprocess("sounds good", CLOUD, ed)[1] is True       # a clean sentence: checked too
    ed = FakeEditor("Sounds good.")
    assert postprocess("sounds good", LOCAL, ed, screen=SCREEN)[1] is False and ed.calls == []
    ed = FakeEditor("Meet on Wednesday.")
    postprocess("meet on tuesday no wait wednesday", LOCAL, ed, screen=SCREEN)
    assert ed.calls == [None]                                     # the Mac model never gets the screen


def test_words_from_the_screen_may_fix_a_mishearing_but_not_replace_what_you_said():
    assert not faithful_edit("riding them", "writing them")      # half the words changed
    assert faithful_edit("riding them", "writing them", nearby=SCREEN)  # but "writing" is on screen
    assert not faithful_edit("ok", "I'm supposed to be writing the suggestions on the scripts.",
                             nearby=SCREEN)


def test_claude_is_told_what_the_screen_is_for():
    msgs = AIEditor._messages("clean_screen", AIEditor._clean_input("general", [], "hi", (), "Sam: hello"))
    assert "<screen>" in msgs[0]["content"] and "Never copy text from it" in msgs[0]["content"]
    assert "<screen>\nSam: hello\n</screen>" in msgs[-1]["content"]
    assert "<screen>" not in AIEditor._messages("clean", "x")[0]["content"]
    assert AIEditor.clean_kind([("a", "b")], "s") == "clean_hints_screen"


def test_foni_reads_as_you_start_and_uses_it_at_the_end(monkeypatch):
    import foni.app as appmod
    from foni.app import Foni
    monkeypatch.setattr(appmod.screen_context, "read",
                        lambda: {"app": "Claude", "screen": "Aoife: see you at Baseten", "seconds": 0.01})
    app = types.SimpleNamespace(cfg=dict(DEFAULT_CONFIG), context={"press": None},
                                dictionary=types.SimpleNamespace(entries={}))
    for m in ("_read_context", "_context_for", "_your_words"):
        setattr(app, m, getattr(Foni, m).__get__(app))
    app._read_context(7)
    ctx = app._context_for(app.context)
    assert ctx["screen"] == "Aoife: see you at Baseten" and set(ctx["names"]) == {"Aoife", "Baseten"}
    assert app._your_words(ctx["names"])[-2:] == ctx["names"]
    app.cfg["read_context"] = False                               # menu → turned off
    app.context = {"press": None}
    app._read_context(8)
    assert app.context == {"press": None}
