"""Speech to text, on this Mac (Parakeet, or Whisper) with MLX on the Apple GPU."""
import difflib
import re
import numpy as np
import time
from .audio import SAMPLE_RATE
from .models import model_path


# ---------------------------------------------------------------- speech -> text

def split_at_pauses(audio, longest=90, shortest=45):
    """Cut a long recording into pieces of `shortest`-`longest` seconds, each cut at the
    quietest moment (where you paused), so no word is split and nothing has to be stitched."""
    frame = SAMPLE_RATE // 50  # 20 ms
    pieces, start = [], 0
    while len(audio) - start > longest * SAMPLE_RATE:
        lo = start + shortest * SAMPLE_RATE
        seg = audio[lo:start + longest * SAMPLE_RATE]
        n = len(seg) // frame
        energy = np.sqrt(np.mean(seg[:n * frame].reshape(n, frame) ** 2, axis=1))
        quiet = np.convolve(energy, np.ones(15), "valid")  # 0.3 s stretches
        cut = lo + (int(np.argmin(quiet)) + 7) * frame
        pieces.append(audio[start:cut])
        start = cut
    pieces.append(audio[start:])
    return pieces


# ---------------------------------------------------------------- your words

def _spelled_as_heard(s):
    """Spelling evened out by sound: "phony" and "Foni" both become "foni"."""
    s = s.lower()
    for a, b in (("ph", "f"), ("gh", ""), ("ck", "k"), ("qu", "kw"), ("x", "ks"), ("c", "k"),
                 ("q", "k"), ("z", "s"), ("y", "i")):
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]", "", s))


def _consonants(s):
    s = _spelled_as_heard(s)
    return s[:1] + re.sub(r"[aeiouhw]", "", s[1:])


def _one_apart(a, b):
    """At most one letter added, dropped or changed."""
    if abs(len(a) - len(b)) > 1:
        return False
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1] <= 1


def sounds_like_your_words(text, vocab):
    """[(what was typed, your word)] where the text sounds like one of your words but isn't
    written like it: "phony" for Foni, "test flight" for TestFlight, "cap cut" for CapCut."""
    for v in sorted(vocab, key=len, reverse=True):  # already written right: leave it out
        text = re.sub(rf"(?<![\w-]){re.escape(v)}(?![\w-])", " | ", text)
    words = re.findall(r"[\w'-]+|\|", text)
    found = []
    for v in vocab:
        heard, skeleton, n = _spelled_as_heard(v), _consonants(v), len(v.split())
        if len(heard) < 4:
            continue  # AI, RAM: too short to judge by sound
        for size in range(max(1, n - 1), n + 2):
            for i in range(len(words) - size + 1):
                chunk = " ".join(words[i:i + size])
                if "|" in chunk or chunk.strip("'-").lower() in {"a", "the", "and"}:
                    continue
                if (difflib.SequenceMatcher(None, _spelled_as_heard(chunk), heard).ratio() >= 0.85
                        or (len(skeleton) >= 4 and _one_apart(_consonants(chunk), skeleton))):
                    found.append((chunk, v))
    return found


def _text(tokens):
    return "".join(t.text for t in tokens).strip()


PIECE = 30  # seconds: longer dictations are transcribed in pieces cut where you paused


def join_pieces(texts):
    """Join pieces cut at pauses. A pause isn't always a full stop, so "and" after a piece
    that didn't end a sentence stays "and", not "And"."""
    from .learning import COMMON
    out = ""
    for t in (t.strip() for t in texts):
        if not t:
            continue
        first = t.split()[0]
        if out and out[-1] not in ".?!:" and first.lower() in COMMON and first != "I" \
                and first[:1].isupper() and first[1:].islower():
            t = first.lower() + t[len(first):]
        out = f"{out} {t}" if out else t
    return out


def boostable(vocab):
    """Your words worth favouring when listening again. Not short ones: favouring "AI" turned
    "I want" into "AI want" (those are left to the learned-words list, which matches whole words)."""
    return [w for w in vocab if len(_spelled_as_heard(w)) >= 4]


class Transcriber:
    BOOST = 4.0  # how strongly a re-listen favours your words (tested: 7.4% -> 1.4% word errors)
    BEAM = 3     # guesses kept at once while re-listening

    def __init__(self, cfg, on_progress=None):
        self.cfg = cfg
        self.engine = cfg["engine"]
        t = time.time()
        if self.engine == "parakeet":
            import mlx.core as mx
            from parakeet_mlx import from_pretrained
            from parakeet_mlx.audio import get_logmel
            self.mx, self.get_logmel = mx, get_logmel
            self.model = from_pretrained(model_path(cfg["parakeet_model"], on_progress))
            self.boosters = {}
        else:
            import mlx_whisper
            self.mlx_whisper = mlx_whisper
            self.whisper_path = model_path(cfg["whisper_model"], on_progress)
        # warm-up: loads the weights and compiles the GPU kernels before your first dictation
        self.transcribe(np.random.default_rng(0).normal(0, 0.01, SAMPLE_RATE * 2).astype(np.float32))
        print(f"[model] {self.engine} ready in {time.time() - t:.1f}s")

    def transcribe(self, audio, vocab=()):
        """`vocab`: your words (names, brands, jargon). When the text sounds like one of them
        but isn't written like it, Foni listens again, favouring your words."""
        pad = np.zeros(int(SAMPLE_RATE * 0.15), dtype=np.float32)  # clean edges help the model
        audio = np.concatenate([pad, audio.astype(np.float32), pad])
        if self.engine == "parakeet":
            return self._parakeet(audio, tuple(vocab))
        return self._whisper(audio)["text"].strip()

    def _parakeet(self, audio, vocab=()):
        if len(audio) > SAMPLE_RATE * PIECE:  # long: in pieces, each cut where you paused
            return join_pieces(self.piece(p, vocab) for p in split_at_pauses(audio, PIECE, PIECE // 2))
        return self.piece(audio, vocab)

    def piece(self, audio, vocab=()):
        """Up to PIECE seconds of speech -> text. The fast way first; if something sounds like one
        of your words but isn't written like it, listen to this piece again favouring your words
        (the audio analysis is reused, so only the last step is repeated)."""
        from parakeet_mlx.parakeet import DecodingConfig
        mx = self.mx
        mel = self.get_logmel(mx.array(audio, dtype=mx.float32), self.model.preprocessor_config)
        features, lengths = self.model.encoder(mel if mel.ndim == 3 else mx.expand_dims(mel, 0))
        mx.eval(features, lengths)
        text = _text(self._decode(features, lengths, DecodingConfig()))
        near = sounds_like_your_words(text, vocab)
        if near:
            again = _text(self._decode(features, lengths, self._booster(vocab)))
            print(f"[asr] sounded like your words ({', '.join(f'{a} ~ {b}' for a, b in near)}); "
                  f"listened again: {'changed' if again != text else 'kept as heard'}")
            text = again
        return text

    def _decode(self, features, lengths, config):
        result, _ = self.model.decode(features, lengths, config=config)
        return result[0]

    def _booster(self, vocab):
        if vocab not in self.boosters:
            from parakeet_mlx import boost
            from parakeet_mlx.parakeet import Beam, DecodingConfig
            words = boost.words({w: self.BOOST for w in boostable(vocab)})
            self.boosters[vocab] = DecodingConfig(decoding=Beam(
                beam_size=self.BEAM, booster=boost.lift(words, self.model.vocabulary)))
        return self.boosters[vocab]

    def _whisper(self, audio):
        vocab = self.cfg.get("vocabulary") or []
        return self.mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.whisper_path,
            language=self.cfg.get("language"),
            initial_prompt=(", ".join(vocab) + ".") if vocab else None,
            condition_on_previous_text=False,
            temperature=0.0,
            verbose=None,
        )
