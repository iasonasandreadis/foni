import difflib
import json
import re
import time
from .config import LEARNED_PATH


# ---------------------------------------------------------------- learning dictionary

class Dictionary:
    """Words learned from your corrections: {"rabbits": {"to": "RAM", "count": 1, "t": ...}}.
    Stored in ~/.foni/dictionary.json; delete a line there to forget a word."""

    def __init__(self):
        self.entries, self.mtime = {}, None
        self.reload()

    def reload(self):
        try:
            mtime = LEARNED_PATH.stat().st_mtime
            if mtime != self.mtime:
                self.entries, self.mtime = json.loads(LEARNED_PATH.read_text()), mtime
        except FileNotFoundError:
            self.entries = {}
        except Exception as e:
            print(f"[dictionary] could not read {LEARNED_PATH}: {e}")

    def add(self, heard, meant):
        self.reload()
        key = heard.lower()
        e = self.entries.get(key)
        if e and e["to"] == meant:
            e["count"] += 1
        else:
            e = self.entries[key] = {"to": meant, "count": 1}
        e["t"] = round(time.time())
        LEARNED_PATH.write_text(json.dumps(self.entries, indent=2, ensure_ascii=False))
        self.mtime = LEARNED_PATH.stat().st_mtime
        return e["count"]

    def hints_for(self, text):
        self.reload()
        return [(h, e["to"], e["count"]) for h, e in self.entries.items()
                if re.search(rf"\b{re.escape(h)}\b", text, re.IGNORECASE)]


def _words(s):
    return re.findall(r"\S+", s)


def _bare(w):
    return re.sub(r"^[^\w]+|[^\w]+$", "", w)


def _tokens(s):
    return re.findall(r"\w+", s.lower())


def _contains(big, small):
    """`small` appears, in order and side by side, inside `big`."""
    return bool(small) and any(big[k:k + len(small)] == small
                               for k in range(len(big) - len(small) + 1))


# Words you swap for one another when you change your mind, not because they were mis-heard.
SAME_KIND = (
    {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
     "today", "tomorrow", "yesterday", "tonight", "morning", "afternoon", "evening"},
    {"january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"},
    {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
     "eleven", "twelve", "twenty", "thirty", "hundred", "thousand"} | {str(n) for n in range(1000)},
)


def find_corrections(before, after, pasted):
    """Word swaps you made inside the text Foni just pasted, that look like fixes of a
    mis-heard word ("rabbits" -> "RAM"), not rewrites. Returns [(heard, meant)]."""
    a, b, p = _words(before), _words(after), _words(pasted)
    start = next((k for k in range(len(a) - len(p) + 1) if a[k:k + len(p)] == p), None)
    if start is None or not p:
        return []
    end = start + len(p)
    found = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "replace" or i1 < start or i2 > end or i2 - i1 > 3 or j2 - j1 > 3:
            continue
        old = " ".join(_bare(w) for w in a[i1:i2]).strip()
        new = " ".join(_bare(w) for w in b[j1:j2]).strip()
        if not old or not new or old == new or not any(c.isalpha() for c in new):
            continue
        if any(old.lower() in kind and new.lower() in kind for kind in SAME_KIND):
            continue  # Friday -> Monday, 3 -> 4: you changed your mind, it wasn't mis-heard
        if _contains(_tokens(new), _tokens(old)) or _contains(_tokens(old), _tokens(new)):
            continue  # "called." -> "called Foni": you added (or cut) words, nothing was mis-heard
        similar = difflib.SequenceMatcher(None, old.lower(), new.lower()).ratio()
        if similar < 0.3 and old[0].lower() != new[0].lower():
            continue  # a different word altogether: a rewrite, not a mis-hearing
        found.append((old, new))
    return found
