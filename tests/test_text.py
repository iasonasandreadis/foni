"""Clean-up of what you said, writing styles per app, and the learning dictionary."""
from foni.config import DEFAULT_CONFIG
from foni.learning import find_corrections
from foni.text import (
    apply_style,
    dedupe_repeats,
    faithful_edit,
    needs_ai,
    plausible_edit,
    postprocess,
    style_group,
)

CFG = dict(DEFAULT_CONFIG, ai_editing="smart", ai_engine="local")


class FakeEditor:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def clean(self, text, style, vocab, corrections=()):
        self.calls += 1
        return self.reply


# ---- clean-up without AI
def test_fillers_removed():
    assert postprocess("um so I think uh we should go", CFG)[0] == "So I think we should go"


def test_spoken_new_line():
    assert postprocess("first line new line second line", CFG)[0] == "First line\nSecond line"


def test_snippet():
    cfg = dict(CFG, snippets={"my email": "me@example.com"})
    assert postprocess("send it to my email", cfg)[0] == "Send it to me@example.com"


def test_hallucination_dropped():
    assert postprocess("Thank you.", CFG) == ("", False)


def test_repeats_removed():
    assert dedupe_repeats("I I think the the plan works") == "I think the plan works"


# ---- when the AI is used, and its safety checks
def test_clean_sentence_skips_ai():
    ed = FakeEditor("anything")
    assert postprocess("This sentence is already clean.", CFG, ed)[1] is False
    assert ed.calls == 0


def test_self_correction_uses_ai():
    assert needs_ai("meet on tuesday no wait wednesday")
    ed = FakeEditor("Meet on Wednesday.")
    assert postprocess("meet on tuesday no wait wednesday", CFG, ed) == ("Meet on Wednesday.", True)


def test_ai_answering_is_rejected():
    assert not plausible_edit("what is the capital of france", "Here is the answer: Paris.")


def test_ai_dropping_a_question_is_rejected():
    assert not plausible_edit("are you coming? I hope so", "You are coming. I hope so.")


def test_ai_rewording_is_rejected():
    src = "send the invoice to mark sorry I mean to sarah by the end of the day"
    assert not faithful_edit(src, "Please forward the bill to Sarah before close of business.")
    assert faithful_edit(src, "Send the invoice to Sarah by the end of the day.")


def test_greek_skips_mac_ai_but_not_cloud_ai():
    text = "Ε λοιπόν θέλω να να πάμε για καφέ"
    ed = FakeEditor("Θέλω να πάμε για καφέ.")
    assert postprocess(text, CFG, ed)[1] is False
    for engine in ("cloud", "openai"):
        assert postprocess(text, dict(CFG, ai_engine=engine), FakeEditor("Λοιπόν, θέλω να πάμε για καφέ."))[1]


# ---- writing style per app
def test_style_groups():
    assert style_group("WhatsApp") == "personal"
    assert style_group("Slack") == "work"
    assert style_group("Mail") == "email"
    assert style_group("Notes") == "other"
    assert style_group("Claude") == "ai" and style_group("Terminal") == "ai"  # Claude Code too
    assert style_group("Cursor") is None and style_group("Xcode") is None   # code: never changed


def test_styles():
    s = "Yeah I'm coming. See you at 8."
    assert apply_style(s, "formal") == s
    assert apply_style(s, "casual") == "Yeah I'm coming. See you at 8"
    assert apply_style(s, "very_casual") == "yeah I'm coming. see you at 8"
    assert apply_style(s, "excited") == "Yeah I'm coming. See you at 8!"


def test_styles_keep_questions_acronyms_and_names():
    assert apply_style("Are you free?", "very_casual") == "are you free?"
    assert apply_style("RAM is fine. MacBook works.", "very_casual") == "RAM is fine. MacBook works"
    assert apply_style("Nikos said hi.", "very_casual", keep={"Nikos"}) == "Nikos said hi"
    assert apply_style("Well...", "casual") == "Well..."


def test_styles_greek():
    assert apply_style("Θα έρθω σε λίγο. Έλα.", "very_casual") == "θα έρθω σε λίγο. έλα"
    assert apply_style("Τι ώρα ξεκινάει;", "casual") == "Τι ώρα ξεκινάει;"


# ---- learning dictionary
def test_learns_a_misheard_word():
    pasted = "My Mac has sixteen gigs of rabbits."
    before = f"Hi. {pasted}"
    after = "Hi. My Mac has sixteen gigs of RAM."
    assert find_corrections(before, after, pasted) == [("rabbits", "RAM")]


def test_ignores_a_rewrite():
    pasted = "Let's meet on Friday."
    assert find_corrections(pasted, "Let's meet on Monday.", pasted) == []


def test_ignores_words_you_add_after_it():
    # you dictate, then keep typing: that's not a mis-heard word
    pasted = "I want to make a video about my new app called."
    for after in ("I want to make a video about my new app called https://jayandreadis.com/foni",
                  "I want to make a video about my new app called Foni.",
                  "I want to make a video about my new app, so called."):
        assert find_corrections(pasted, after, pasted) == [], after
    pasted = "Ask Steven."
    assert find_corrections(pasted, "Ask Steven/Alex.", pasted) == []


def test_ignores_words_you_cut():
    pasted = "It was really very good."
    assert find_corrections(pasted, "It was good.", pasted) == []


def test_still_learns_joined_and_split_words():
    pasted = "Upload it to test flight."
    assert find_corrections(pasted, "Upload it to TestFlight.", pasted) == [("test flight", "TestFlight")]
    pasted = "I paid for the clawed subscription."
    assert find_corrections(pasted, "I paid for the Claude subscription.", pasted) == [("clawed", "Claude")]
