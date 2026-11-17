"""AI in the cloud, with your own account: Claude, or any OpenAI-style service."""
import getpass
import json
import subprocess
import threading
import time
from datetime import date
from .ai_local import AIEditor
from .config import SPEND_PATH


CLAUDE_KEY = "Foni Claude API key"          # Keychain entries holding your keys
OPENAI_KEY = "Foni OpenAI-style API key"
OLD_CLAUDE_KEY = "LocalFlow Claude API key"  # Foni's name during development


def keychain_get(service=CLAUDE_KEY):
    """A key from the macOS Keychain (None if you haven't set one)."""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", service,
                            "-a", getpass.getuser(), "-w"], capture_output=True, text=True, timeout=5)
        key = r.stdout.strip()
        return key if r.returncode == 0 and key else None
    except Exception:
        return None


def keychain_set(key, service=CLAUDE_KEY):
    r = subprocess.run(["security", "add-generic-password", "-U", "-s", service,
                        "-a", getpass.getuser(), "-w", key], capture_output=True, text=True, timeout=5)
    return r.returncode == 0


def migrate_old_key():
    """Carry a Claude key saved under Foni's development name over to Foni."""
    if keychain_get(CLAUDE_KEY) is None:
        old = keychain_get(OLD_CLAUDE_KEY)
        if old:
            keychain_set(old, CLAUDE_KEY)


# Claude's prices in $ per million tokens: (input, output, cache read). Writing to the prompt
# cache costs 1.25x input. (Haiku 5.5's rate is for prompts under 100K tokens; a dictation is ~700.)
CLAUDE_PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
}


def fmt_usd(v):
    if v <= 0:
        return "$0.00"
    if v < 0.0001:
        return "under $0.0001"
    return f"${v:.4f}" if v < 0.01 else f"${v:.2f}"


class ClaudeSpend:
    """What Claude has actually charged, added up from the token counts on every reply
    (the same numbers the bill is made from). Kept per day in ~/.foni/claude_spend.json."""

    def __init__(self, path=None):
        self.path = path or SPEND_PATH
        self.lock = threading.Lock()
        self.days = {}             # "2026-11-10" -> {"calls", "input", "output", "usd"}
        self.before = None         # optional estimate for use before tracking existed
        self.changes = 0           # bumps on every update, so the menu knows to redraw
        try:
            data = json.loads(self.path.read_text())
            self.days = data.get("days", {})
            self.before = data.get("before_tracking")
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[spend] could not read {self.path}: {e}")

    def add(self, model, usage, edit=True):
        """Record one reply. `edit` is False for the key test, which costs but isn't an edit."""
        n = lambda name: int(getattr(usage, name, 0) or 0)
        inp, out = n("input_tokens"), n("output_tokens")
        c_write, c_read = n("cache_creation_input_tokens"), n("cache_read_input_tokens")
        price = CLAUDE_PRICES.get(model)
        if price is None:  # unknown model: count it at the priciest known rate, never under
            price = max(CLAUDE_PRICES.values())
        p_in, p_out, p_read = price
        usd = (inp * p_in + c_write * p_in * 1.25 + c_read * p_read + out * p_out) / 1e6
        with self.lock:
            d = self.days.setdefault(date.today().isoformat(),
                                     {"calls": 0, "input": 0, "output": 0, "usd": 0.0})
            d["calls"] += int(edit)
            d["input"] += inp + c_write + c_read
            d["output"] += out
            d["usd"] += usd
            self.changes += 1
            self._save()
        return usd

    def _save(self):
        try:
            data = {"days": self.days}
            if self.before:
                data["before_tracking"] = self.before
            self.path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"[spend] could not save: {e}")

    def reset(self):
        with self.lock:
            self.days, self.before = {}, None
            self.changes += 1
            self._save()

    def summary(self):
        """Totals for the menu: today, this month, all time, and a monthly pace."""
        today = date.today()
        with self.lock:
            days = {k: dict(v) for k, v in self.days.items()}
            before = dict(self.before) if self.before else None
        pick = lambda keep: (sum(v["usd"] for k, v in days.items() if keep(k)),
                             sum(v["calls"] for k, v in days.items() if keep(k)))
        s = {"today": pick(lambda k: k == today.isoformat()),
             "month": pick(lambda k: k.startswith(today.isoformat()[:7])),
             "total": pick(lambda k: True), "before": before}
        usd, calls = s["total"]
        first = min(days) if days else None
        if before:
            usd, calls = usd + before.get("usd", 0.0), calls + before.get("calls", 0)
            if (before.get("since") or "").startswith(today.isoformat()[:7]):
                m_usd, m_calls = s["month"]
                s["month"] = (m_usd + before.get("usd", 0.0), m_calls + before.get("calls", 0))
            starts = [d for d in (first, before.get("since")) if d]
            first = min(starts) if starts else None
        s["all"] = (usd, calls)
        s["since"] = first
        if first:
            elapsed = (today - date.fromisoformat(first)).days + 1
            s["per_month"] = usd / elapsed * 30
        else:
            s["per_month"] = 0.0
        return s


class CloudEditor:
    """AI editing with Claude Haiku: smarter than the local model, fluent in Greek, and uses no
    RAM on your Mac. Only the text of a dictation that needs editing is sent, never audio.
    Whenever Claude can't be reached, it quietly uses the local model instead."""

    def __init__(self, api_key, model, fallback, meter=None):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, timeout=8.0, max_retries=1)
        self.model, self.fallback, self.meter = model, fallback, meter
        self.broken = None  # set when the key itself is rejected, so we stop trying it

    def _record(self, r, edit=True):
        if self.meter is not None and getattr(r, "usage", None) is not None:
            usd = self.meter.add(self.model, r.usage, edit)
            u = r.usage
            print(f"[spend] {u.input_tokens} in / {u.output_tokens} out tokens = {fmt_usd(usd)}")

    def _ask(self, kind, user, max_tokens):
        msgs = AIEditor._messages(kind, user)  # same instructions and examples as the local model
        r = self.client.messages.create(
            model=self.model, max_tokens=max_tokens,
            system=msgs[0]["content"], messages=msgs[1:],
            thinking={"type": "disabled"},  # a cleanup needs speed, not deliberation
            cache_control={"type": "ephemeral"})
        self._record(r)  # a declined reply is still billed, so count it first
        if r.stop_reason == "refusal":
            raise RuntimeError("Claude declined this one")
        return "".join(b.text for b in r.content if b.type == "text").strip()

    def _run(self, kind, user, max_tokens, local_call):
        if not self.broken:
            t = time.time()
            try:
                out = self._ask(kind, user, max_tokens)
                print(f"[cloud] {self.model} edited in {time.time() - t:.2f}s")
                return out
            except (self.anthropic.AuthenticationError, self.anthropic.PermissionDeniedError) as e:
                self.broken = "the API key was rejected"
                print(f"[cloud] {self.broken} ({type(e).__name__}); using the local model")
            except Exception as e:
                print(f"[cloud] unavailable ({type(e).__name__}); using the local model this time")
        ed = self.fallback()
        return local_call(ed) if ed else None

    def clean(self, text, style, vocab, corrections=()):
        kind = "clean_hints" if corrections else "clean"
        out = self._run(kind, AIEditor._clean_input(style, vocab, text, corrections),
                        min(1200, 3 * len(text.split()) + 60),
                        lambda ed: ed.clean(text, style, vocab, corrections))
        return out if out is not None else text

    def command(self, instruction, selected):
        out = self._run("command", AIEditor._command_input(instruction, selected or ""), 1500,
                        lambda ed: ed.command(instruction, selected))
        return out or ""

    def ping(self):
        """A tiny test request. None if the key works, otherwise a short reason."""
        a = self.anthropic
        try:
            r = self.client.messages.create(model=self.model, max_tokens=8,
                                            thinking={"type": "disabled"},
                                            messages=[{"role": "user", "content": "Reply with OK."}])
            self._record(r, edit=False)
            return None
        except a.AuthenticationError:
            return "the key was rejected"
        except a.PermissionDeniedError:
            return "this key isn't allowed to use the API"
        except a.NotFoundError:
            return f"{self.model} isn't available to this key"
        except a.RateLimitError:
            return "rate limited"
        except a.APIConnectionError:
            return "couldn't reach Claude (internet?)"
        except a.APIStatusError as e:
            return f"Claude said: {getattr(e, 'message', str(e))[:80]}"
        except Exception as e:
            return type(e).__name__


class OpenAIEditor:
    """AI editing through any OpenAI-style service: OpenAI, OpenRouter, Groq, or a local
    Ollama / LM Studio server. Same instructions and safety checks as Claude and the Mac model;
    when the service can't be reached, the Mac model steps in."""

    PRESETS = (  # (name, address); shown when you set it up
        ("OpenAI", "https://api.openai.com/v1"),
        ("OpenRouter", "https://openrouter.ai/api/v1"),
        ("Groq", "https://api.groq.com/openai/v1"),
        ("Ollama (on this Mac)", "http://localhost:11434/v1"),
        ("LM Studio (on this Mac)", "http://localhost:1234/v1"),
    )

    def __init__(self, base_url, model, api_key, fallback):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.key, self.fallback = model, api_key, fallback
        self.broken = None
        self.dropped = set()  # parameters this service rejected (some models refuse temperature)

    def _post(self, body):
        import urllib.request
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())

    def _complete(self, messages, max_tokens):
        import urllib.error
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
        for _ in range(3):  # adapt once or twice to what this service accepts
            for p in self.dropped:
                body.pop(p, None)
            if "max_tokens" in self.dropped:
                body["max_completion_tokens"] = max_tokens
            try:
                data = self._post(body)
                return (data["choices"][0]["message"].get("content") or "").strip()
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="ignore")[:300]
                if e.code == 400 and "temperature" in detail and "temperature" not in self.dropped:
                    self.dropped.add("temperature")
                    continue
                if e.code == 400 and "max_completion_tokens" in detail and "max_tokens" not in self.dropped:
                    self.dropped.add("max_tokens")
                    continue
                raise RuntimeError(f"{e.code}: {detail}") from e
        raise RuntimeError("the service rejected the request")

    def _run(self, kind, user, max_tokens, local_call):
        if not self.broken:
            t = time.time()
            try:
                out = self._complete(AIEditor._messages(kind, user), max_tokens)
                print(f"[cloud] {self.model} edited in {time.time() - t:.2f}s")
                return out
            except RuntimeError as e:
                if str(e).startswith(("401", "403")):
                    self.broken = "the API key was rejected"
                print(f"[cloud] {self.model} unavailable ({str(e)[:80]}); using the local model")
            except Exception as e:
                print(f"[cloud] unavailable ({type(e).__name__}); using the local model this time")
        ed = self.fallback()
        return local_call(ed) if ed else None

    def clean(self, text, style, vocab, corrections=()):
        kind = "clean_hints" if corrections else "clean"
        out = self._run(kind, AIEditor._clean_input(style, vocab, text, corrections),
                        min(1200, 3 * len(text.split()) + 60),
                        lambda ed: ed.clean(text, style, vocab, corrections))
        return out if out is not None else text

    def command(self, instruction, selected):
        out = self._run("command", AIEditor._command_input(instruction, selected or ""), 1500,
                        lambda ed: ed.command(instruction, selected))
        return out or ""

    def ping(self):
        """A tiny test request. None if it works, otherwise a short reason."""
        try:
            self._complete([{"role": "user", "content": "Reply with OK."}], 8)
            return None
        except RuntimeError as e:
            code = str(e)[:3]
            return {"401": "the key was rejected", "403": "this key isn't allowed to use it",
                    "404": f"the model \"{self.model}\" or the address wasn't found",
                    "429": "rate limited"}.get(code, f"the service said: {str(e)[:80]}")
        except Exception as e:
            return f"couldn't reach the service ({type(e).__name__})"
