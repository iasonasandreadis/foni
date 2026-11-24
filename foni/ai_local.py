"""The AI that runs on this Mac (a small Qwen model with MLX): private, offline, free."""
import time
from .models import model_path
from .prompts import (
    CLEAN_SHOTS,
    CLEAN_SYSTEM,
    COMMAND_SHOTS,
    COMMAND_SYSTEM,
    HINTS_SHOTS,
    HINTS_SYSTEM,
    NOTES_SHOTS,
    NOTES_SYSTEM,
)


# ---------------------------------------------------------------- AI editor (local LLM)

class AIEditor:
    """Small local LLM. The fixed instructions are pre-computed once (prompt cache), so each
    request only pays for the new transcript plus the generated text."""

    def __init__(self, model_name, draft_name=None, warm=True, on_progress=None):
        import mlx.core as mx
        from mlx_lm import load, stream_generate
        from mlx_lm.models.cache import make_prompt_cache, trim_prompt_cache
        from mlx_lm.sample_utils import make_sampler
        t = time.time()
        self.mx, self.stream_generate = mx, stream_generate
        self.make_cache, self.trim_cache = make_prompt_cache, trim_prompt_cache
        self.model, self.tok = load(model_path(model_name, on_progress))
        # Speculative decoding: a tiny model drafts tokens, the big one verifies them in one
        # pass. Output is the same as the big model alone; cleanup is mostly copying, so the
        # drafts are nearly always accepted.
        self.draft = load(model_path(draft_name))[0] if draft_name else None
        self.sampler = make_sampler(temp=0.0)
        self.caches = {}
        self._prefix("clean")  # dictionary & voice-command instructions are cached on first use
        if warm:  # first load only: compiles GPU kernels, which stay compiled after unloading
            self.clean("um hello there", "general", [])
        print(f"[model] AI editor ready in {time.time() - t:.1f}s")

    @staticmethod
    def _messages(kind, user):
        if kind in ("clean", "clean_hints"):
            system, shots = ((CLEAN_SYSTEM, CLEAN_SHOTS) if kind == "clean"
                             else (HINTS_SYSTEM, HINTS_SHOTS))
            msgs = [{"role": "system", "content": system}]
            for style, raw, out, *corr in shots:
                msgs += [{"role": "user", "content": AIEditor._clean_input(
                    style, [], raw, corr[0] if corr else ())},
                         {"role": "assistant", "content": out}]
        elif kind == "notes":
            msgs = [{"role": "system", "content": NOTES_SYSTEM}]
            for said, out in NOTES_SHOTS:
                msgs += [{"role": "user", "content": AIEditor._notes_input(said)},
                         {"role": "assistant", "content": out}]
        else:
            msgs = [{"role": "system", "content": COMMAND_SYSTEM}]
            for instr, sel, out in COMMAND_SHOTS:
                msgs += [{"role": "user", "content": AIEditor._command_input(instr, sel)},
                         {"role": "assistant", "content": out}]
        return msgs + [{"role": "user", "content": user}]

    @staticmethod
    def _clean_input(style, vocab, text, corrections=()):
        v = f"\nVocabulary: {', '.join(vocab)}" if vocab else ""
        c = ("\nCorrections: " + "; ".join(f"{h} → {m}" for h, m in corrections)
             if corrections else "")
        return f"Context: {style}{v}{c}\n<transcript>\n{text}\n</transcript>"

    @staticmethod
    def _notes_input(transcript):
        return f"<transcript>\n{transcript}\n</transcript>"

    @staticmethod
    def _command_input(instruction, selected):
        return f"<instruction>\n{instruction}\n</instruction>\n<selected>\n{selected}\n</selected>"

    def _tokens(self, kind, user):
        return list(self.tok.apply_chat_template(self._messages(kind, user),
                                                 add_generation_prompt=True))

    def _prefix(self, kind):
        if kind not in self.caches:
            a = self._tokens(kind, "Context: a\n<transcript>\nx\n</transcript>")
            b = self._tokens(kind, "<instruction>\ny\n</instruction>")
            n = 0
            while n < min(len(a), len(b)) and a[n] == b[n]:
                n += 1
            prefix = a[:n]
            caches = []
            for m in (self.model, self.draft):
                if m is None:
                    continue
                c = self.make_cache(m)
                m(self.mx.array(prefix)[None], cache=c)
                self.mx.eval([x.state for x in c])
                caches.append(c)
            self.caches[kind] = (prefix, caches)
        return self.caches[kind]

    def _generate(self, kind, user, max_tokens):
        prefix, caches = self._prefix(kind)
        full = self._tokens(kind, user)
        cached = full[:len(prefix)] == prefix
        if cached:
            prompt, use = full[len(prefix):], [x for c in caches for x in c]
        else:  # should not happen; fall back to an uncached run
            prompt = full
            use = self.make_cache(self.model) + (self.make_cache(self.draft) if self.draft else [])
        if self.draft:
            out = "".join(r.text for r in self.stream_generate(
                self.model, self.tok, prompt, max_tokens=max_tokens, sampler=self.sampler,
                prompt_cache=use, draft_model=self.draft, num_draft_tokens=4))
        else:
            out = self._lookup_generate(prompt, use[:len(self.model.layers)], max_tokens)
        if cached:  # roll the caches back to just the fixed instructions
            for c in caches:
                self.trim_cache(c, c[0].offset - len(prefix))
        return out.strip()

    def _lookup_generate(self, prompt, cache, max_tokens, k=4):
        """Greedy decoding with prompt-lookup drafts. Cleaned-up text is mostly a copy of the
        transcript, so we guess the next k tokens by finding the latest generated words in the
        prompt and continuing from there; one forward pass verifies all guesses at once.
        Output is identical to plain greedy decoding, just several times faster."""
        mx = self.mx
        eos = set(self.tok.eos_token_ids)
        logits = self.model(mx.array(prompt)[None], cache=cache)
        last = int(mx.argmax(logits[0, -1]).item())
        out = []
        while last not in eos and len(out) < max_tokens:
            out.append(last)
            draft = []
            for n in (3, 2):  # longest recent n-gram that also appears in the prompt
                if len(out) < n:
                    continue
                tail = out[-n:]
                for i in range(len(prompt) - n, -1, -1):
                    if prompt[i:i + n] == tail:
                        draft = prompt[i + n:i + n + k]
                        break
                if draft:
                    break
            draft = draft[:max(0, max_tokens - len(out))]
            logits = self.model(mx.array([last] + draft)[None], cache=cache)
            preds = mx.argmax(logits[0], axis=-1).tolist()
            accepted = 0
            while accepted < len(draft) and draft[accepted] == preds[accepted]:
                accepted += 1
            if accepted < len(draft):
                self.trim_cache(cache, len(draft) - accepted)
            for tok in draft[:accepted]:
                if tok in eos or len(out) >= max_tokens:
                    break
                out.append(tok)
            else:
                last = preds[accepted]
                continue
            break
        return self.tok.decode(out)

    def clean(self, text, style, vocab, corrections=()):
        n = len(text.split())
        kind = "clean_hints" if corrections else "clean"
        return self._generate(kind, self._clean_input(style, vocab, text, corrections),
                              min(600, 2 * n + 40))

    def command(self, instruction, selected):
        return self._generate("command", self._command_input(instruction, selected or ""), 700)

    def notes(self, transcript):
        return self._generate("notes", self._notes_input(transcript), 700)
