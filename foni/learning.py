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
                self._forget_rewrites()
        except FileNotFoundError:
            self.entries = {}
        except Exception as e:
            print(f"[dictionary] could not read {LEARNED_PATH}: {e}")

    def _forget_rewrites(self):
        """Older Foni learned edits that weren't mis-heard words ("but" -> "Bet", "his" ->
        "their") and then changed them in every dictation. Drop those (a copy is kept)."""
        bad = sorted(h for h, e in self.entries.items()
                     if not is_mishearing(h, str((e or {}).get("to", ""))))
        if not bad:
            return
        backup = LEARNED_PATH.with_name(f"dictionary.before-cleanup-{time.strftime('%Y%m%d-%H%M%S')}.json")
        backup.write_text(json.dumps(self.entries, indent=2, ensure_ascii=False))
        for h in bad:
            del self.entries[h]
        LEARNED_PATH.write_text(json.dumps(self.entries, indent=2, ensure_ascii=False))
        self.mtime = LEARNED_PATH.stat().st_mtime
        print(f"[dictionary] forgot {len(bad)} edits that weren't mis-heard words "
              f"(copy in {backup.name})")

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
        """Corrections that apply to `text`: ones you've made at least twice, or a name or
        acronym (RAM, UGC, TestFlight) once. One edit of an ordinary word could be a typo or a
        change of mind, and acting on it changed that word in every dictation."""
        self.reload()
        return [(h, e["to"], e["count"]) for h, e in self.entries.items()
                if (e["count"] >= 2 or looks_like_a_name(e["to"]))
                and re.search(rf"\b{re.escape(h)}\b", text, re.IGNORECASE)]


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


# The most common English words (and the pieces of "I'll", "don't"...). A swap where one side
# is only these is you rewriting the sentence, not Foni mis-hearing a word.
COMMON = frozenset("""
a about above after again against ago all almost also always am an and another any anybody
anyone anything are around as ask asked at away back bad be because been before being below
best better between big both bring but by call came can come could day days did different do
does doing done down during each else enough even ever every few find first for from get gets
getting give go goes going gone good got great guess had has have having he her here hers him
his how however i if in into is it its just keep kind know last least less let like little
long look lot lots made make many may maybe me mean might mine more most much must my myself
need never new next no nor not nothing now of off often oh ok okay old on once one only or
other our ours out over own part people please pretty put quite rather really right said same
say see seem should show since so some someone something soon still such sure take tell than
thank thanks that the their theirs them then there these they thing things think this those
though thought through time to today together too two under until up upon us use used very
want was way we well went were what whatever when where whether which while who whom whose
why will with within without work would yeah yes yet you your yours yourself
ll t s re ve d m don didn doesn isn wasn aren weren won can couldn wouldn shouldn haven hasn
im ive id dont didnt doesnt isnt wasnt cant wont thats youre theyre were
cut check mom mum dad
""".split())


def looks_like_a_name(s):
    """RAM, UGC, AI, TestFlight, iPhone, GPT-4: capitals inside a word, or digits."""
    return any(ch.isupper() for w in s.split() for ch in w[1:]) or any(ch.isdigit() for ch in s)


def is_mishearing(old, new):
    """Could `new` be what you said when Foni typed `old`? False for rewrites and typos."""
    lo, ln = old.lower().strip(), new.lower().strip()
    if not lo or not ln or lo == ln or not any(c.isalpha() for c in new):
        return False
    to, tn = _tokens(old), _tokens(new)
    if any(lo in kind and ln in kind for kind in SAME_KIND):
        return False  # Friday -> Monday, 3 -> 4: you changed your mind, it wasn't mis-heard
    if _contains(tn, to) or _contains(to, tn) or ln.startswith(lo) or lo.startswith(ln):
        return False  # "called." -> "called Foni", "removed" -> "remove": an edit, not hearing
    only_old, only_new = [w for w in to if w not in tn], [w for w in tn if w not in to]
    if all(w in COMMON for w in only_old) or all(w in COMMON for w in only_new):
        return False  # "but" -> "bet", "his" -> "their", "in the" -> "while": a rewrite
    if len(to) == len(tn) == 1 and max(len(lo), len(ln)) <= 4 and not looks_like_a_name(new):
        return False  # "bat" -> "bet", "mod" -> "bot": too short to tell from a typo
    similar = difflib.SequenceMatcher(None, lo, ln).ratio()
    if similar < 0.3 and lo[0] != ln[0]:
        return False  # a different word altogether: a rewrite, not a mis-hearing
    return True


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
        if is_mishearing(old, new):
            found.append((old, new))
    return found
