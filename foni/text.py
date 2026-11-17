import difflib
import re


HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "thank you for watching.",
    ".", "bye.", "thanks for watching.", "subtitles by the amara.org community",
}
FILLER_RE = re.compile(r"(?:,\s*)?\b(?:um+|uh+|erm+|uhm+|hmm+)\b[,.]?\s*", re.IGNORECASE)

# Cues that the raw transcript would benefit from the AI editor ("smart" mode).
CUE_RE = re.compile(
    r"\b(?:no wait|wait no|i mean|actually|sorry|scratch that|or rather|let me rephrase|"
    r"correction|no no|first(?:ly)?|second(?:ly)?|third(?:ly)?|number one|bullet point|"
    r"όχι|δηλαδή|συγγνώμη|πρώτον|δεύτερον)\b", re.IGNORECASE)
REPEAT_RE = re.compile(r"\b(\w+(?:\s+\w+)?),?\s+\1\b", re.IGNORECASE)
# speech models write cut-off words as stray letters ("it's kind of s it's slower")
STRAY_LETTER_RE = re.compile(r"(?<![\w'’.])(?![aAI]\b)[A-Za-z](?![\w'’.])")
# A "sentence" that's only a correction phrase ("No wait.") is leftover after the AI applied it
DANGLING_CUE_RE = re.compile(
    r"(?:(?<=[.!?])|^)\s*(?:no,? wait|wait,? no|sorry|i mean|scratch that|or rather)[.!,]*\s*(?=$|\n)",
    re.IGNORECASE)
COUNTED_LIST_RE = re.compile(r"\bone\b.*\btwo\b.*\bthree\b", re.IGNORECASE | re.DOTALL)

APP_STYLES = [
    (("Messages", "WhatsApp", "Telegram", "Signal", "Slack", "Discord", "Messenger",
      "Instagram", "Microsoft Teams"), "casual chat message"),
    (("Mail", "Outlook", "Microsoft Outlook", "Spark", "Airmail", "Superhuman", "Mimestream"),
     "email"),
    (("Code", "Visual Studio Code", "Cursor", "Xcode", "Terminal", "iTerm2", "Warp", "Zed",
      "PyCharm", "IntelliJ IDEA", "Windsurf", "Ghostty"), "coding / technical"),
    (("Notes", "Notion", "Obsidian", "Pages", "Microsoft Word", "Bear", "Craft"),
     "document / notes"),
]

def style_for(app):
    for names, style in APP_STYLES:
        if app in names:
            return style
    return "general"


# ---------------------------------------------------------------- writing style per app
# Like Wispr Flow's Styles, but plain text rules (no AI): instant, free, no memory, any language.
STYLE_GROUPS = (  # (setting, menu label, apps)
    ("personal", "Personal messages", ("Messages", "WhatsApp", "Telegram", "Signal", "Messenger",
                                       "Instagram", "Discord", "Snapchat", "Viber", "LINE")),
    ("work", "Work messages", ("Slack", "Microsoft Teams", "Teams", "Google Chat", "Mattermost")),
    ("email", "Email", ("Mail", "Outlook", "Microsoft Outlook", "Spark", "Spark Desktop",
                        "Airmail", "Superhuman", "Mimestream", "Canary Mail")),
    # AI chats, and the terminals Claude Code and Codex run in
    ("ai", "AI apps and terminals", ("Claude", "ChatGPT", "Grok", "Grok Bot", "Perplexity",
                                     "Gemini", "T3 Code (Alpha)", "Codex", "Terminal", "iTerm2",
                                     "Warp", "Ghostty")),
    ("other", "Other apps", ()),
)
# Never restyled: code editors need your words exactly as said.
UNSTYLED_APPS = set(APP_STYLES[2][0]) - set(STYLE_GROUPS[3][2])
WRITING_STYLES = {  # setting: (short name, menu label)
    "formal": ("Formal", "Formal: as you said it, with full stops"),
    "casual": ("Casual", "Casual: no full stop at the end"),
    "very_casual": ("Very casual", "Very casual: no capitals, no full stop"),
    "excited": ("Excited", "Excited: ends with !"),
}
STYLE_CHOICES = {"personal": ("formal", "casual", "very_casual"),  # others: formal/casual/excited
                 "ai": ("formal", "casual", "very_casual")}
SENTENCE_START_RE = re.compile(r"(^|[.!?;]\s+|\n\s*(?:[-•*]\s+|\d+[.)]\s+)?)([^\W\d_][\w'’]*)")


def style_group(app):
    if app in UNSTYLED_APPS:
        return None
    return next((key for key, _, apps in STYLE_GROUPS if app in apps), "other")


def apply_style(text, style, keep=()):
    """Restyle finished dictation. `keep`: words to never lowercase (your vocabulary)."""
    if style not in ("casual", "very_casual", "excited") or not text.strip():
        return text
    body = text.rstrip()
    single_stop = body.endswith(".") and not body.endswith("..")
    if style == "excited":
        return body[:-1] + "!" if single_stop else body
    if single_stop:
        body = body[:-1]
    if style == "very_casual":
        def lower(m):
            word = m.group(2)
            if (word in keep or word in ("I", "I'm", "I’m", "I'll", "I’ll", "I've", "I’ve", "I'd", "I’d")
                    or (len(word) > 1 and word.isupper())        # RAM, OK, NASA
                    or not word[1:].islower() and len(word) > 1):  # MacBook, iPhone
                return m.group(0)
            return m.group(1) + word[0].lower() + word[1:]
        body = SENTENCE_START_RE.sub(lower, body)
    return body


def mostly_non_latin(text):
    letters = [c for c in text if c.isalpha()]
    return sum(not c.isascii() for c in letters) > 0.3 * max(1, len(letters))


def dedupe_repeats(text):
    """Safe fallback cleanup: "my of my RAM" -> "my RAM"-style stutters, "all all" -> "all"."""
    for _ in range(3):
        text = REPEAT_RE.sub(r"\1", text)
    return text


def needs_ai(text, allow_non_latin=False):
    # The bundled 3B model is weak outside English-like languages (e.g. it mangles Greek),
    # so smart mode leaves those transcripts as Parakeet wrote them.
    if mostly_non_latin(text) and not allow_non_latin:
        return False
    return bool(CUE_RE.search(text) or REPEAT_RE.search(text) or COUNTED_LIST_RE.search(text)
                or STRAY_LETTER_RE.search(text) or len(text.split()) > 60)


def plausible_edit(src, out):
    """Reject edits where the model answered, rambled, translated or deleted too much."""
    if not out:
        return False
    sw, ow = len(src.split()), len(out.split())
    if ow > sw * 1.3 + 8 or ow < sw * 0.35:
        return False
    if re.match(r"(?i)(here is|here's|sure|certainly|i'm sorry|as an ai)\b", out):
        return False
    if out.count("?") < src.count("?"):  # never lose a question
        return False

    def foreign(s):
        letters = [c for c in s if c.isalpha()]
        return sum(not c.isascii() for c in letters) / max(1, len(letters))
    return abs(foreign(src) - foreign(out)) < 0.4


def _plain_words(s):
    return re.findall(r"[\w']+", s.replace("’", "'").lower())


def faithful_edit(src, out, allowed=()):
    """A good cleanup only removes things (fillers, stutters, what you corrected away) and
    fixes punctuation; it keeps your words in your order. Rejects edits where more than ~15%
    of the words are new or moved — rewording, swapping names, mixing up corrections.
    `allowed`: words the edit may bring in (vocabulary, learned corrections)."""
    src_w = _plain_words(src)
    present = set(src_w)
    ok_new = {w for phrase in allowed for w in _plain_words(phrase)}
    out_w = [w for w in _plain_words(out)
             if w not in ok_new and not (w.isdigit() and w not in present)]  # list numbers ok
    if not out_w:
        return True
    sm = difflib.SequenceMatcher(None, src_w, out_w, autojunk=False)
    kept_in_order = sum(b.size for b in sm.get_matching_blocks())
    return kept_in_order >= 0.85 * len(out_w)


def apply_phrases(text, mapping):
    for spoken, out in (mapping or {}).items():
        text = re.sub(rf"\s*\b{re.escape(spoken)}\b[.,!?]?\s*", out, text, flags=re.IGNORECASE)
    return text


def postprocess(text, cfg, editor=None, app="", learned=None):
    """Returns (text, used_ai)."""
    text = text.replace("<unk>", "")
    if text.lower().strip() in HALLUCINATIONS:
        return "", False
    if cfg["remove_fillers"]:
        text = FILLER_RE.sub(" ", text)
    text = apply_phrases(text, cfg.get("replacements"))
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    used_ai = False
    mode = cfg["ai_editing"]
    hints = learned.hints_for(text) if learned else []  # [(heard, meant, times corrected)]
    cloud = cfg.get("ai_engine") in ("cloud", "openai")  # cloud AIs handle Greek; the Mac model doesn't
    run_ai = editor is not None and text and (cloud or not mostly_non_latin(text)) and (
        (mode == "always" and len(text.split()) >= 3)
        or (mode == "smart" and (needs_ai(text, allow_non_latin=cloud) or hints)))
    # `editor` may be a function that loads the model on demand (it's freed when idle)
    ed = (editor() if callable(editor) else editor) if run_ai else None
    if ed:
        vocab = list(cfg.get("vocabulary") or []) + [m for _, m, _ in hints]
        edited = ed.clean(text, style_for(app), vocab, [(h, m) for h, m, _ in hints])
        edited = DANGLING_CUE_RE.sub("", edited).strip()
        if plausible_edit(text, edited) and faithful_edit(text, edited, vocab):
            text, used_ai = edited, True
        else:
            print(f"[ai] rejected edit, kept transcript: {edited!r}")
            text = dedupe_repeats(text)
    elif run_ai:
        text = dedupe_repeats(text)
    if not used_ai:  # no AI: apply corrections you've made at least twice, word for word
        for heard, meant, times in hints:
            if times >= 2:
                text = re.sub(rf"\b{re.escape(heard)}\b", meant, text, flags=re.IGNORECASE)
    snippets = cfg.get("snippets") or {}
    for trigger, full in snippets.items():
        text = re.sub(rf"\b{re.escape(trigger)}\b", lambda _m, f=full: f, text,
                      flags=re.IGNORECASE)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    # capitalize the start and anything after a sentence end / line break
    text = re.sub(r"(^|[.!?]\s+|\n)([^\W\d_])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text, used_ai
