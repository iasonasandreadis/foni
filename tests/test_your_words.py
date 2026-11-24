"""Hearing your words (names, brands, jargon) right, and transcribing long dictations while you
talk. Real recordings were measured separately: 36 clips in 6 accents went from 7.4% to 1.6%
word errors, and sentences with look-alike words ("cloud", "a phony review") stayed as they were."""
import time
import types

import numpy as np
import pytest

from foni.app import Foni
from foni.speech import SAMPLE_RATE, Transcriber, boostable, join_pieces, sounds_like_your_words

VOCAB = ("Foni", "Claude Code", "Claude", "UGC", "TestFlight", "Creator Studio", "CapCut", "b-roll")


@pytest.mark.parametrize("text, near", [
    ("I want to make phony smarter", [("phony", "Foni")]),
    ("Open clog code now", [("clog code", "Claude Code")]),
    ("Upload it to test flight", [("test flight", "TestFlight")]),
    ("add captions in cap cut", [("cap cut", "CapCut")]),
    ("types into create a studio", [("create a studio", "Creator Studio")]),
])
def test_hears_what_sounds_like_your_words(text, near):
    assert sounds_like_your_words(text, VOCAB) == near


@pytest.mark.parametrize("text", [
    "Open Claude Code and Foni", "files in the cloud", "that is funny", "call my phone",
    "it is fine", "the cut was clean", "the creator of the studio", "Ask Claude to write it",
])
def test_leaves_ordinary_words_alone(text):
    assert sounds_like_your_words(text, VOCAB) == []


def test_pieces_join_without_a_capital_mid_sentence():
    assert join_pieces(["I went home", "And then I slept.", "Then Claude woke me"]) == \
        "I went home and then I slept. Then Claude woke me"
    assert join_pieces(["Ask", "I think so", "", "Sam knows"]) == "Ask I think so Sam knows"


@pytest.fixture
def transcriber():
    """The real Transcriber with a pretend speech model: the fast pass hears "phony", the
    re-listen favouring your words hears "Foni"."""
    import mlx.core as mx
    t = Transcriber.__new__(Transcriber)
    t.engine, t.mx, t.boosters, t.calls = "parakeet", mx, {}, []
    t.get_logmel = lambda x, cfg: mx.zeros((1, 10, 4))
    t.model = types.SimpleNamespace(preprocessor_config=None, encoder=lambda mel: (mx.zeros((1, 3, 2)), mx.array([3])))
    t._booster = lambda vocab: "boosted"

    def decode(features, lengths, config):
        t.calls.append(config)
        said = "Foni works" if config == "boosted" else "phony works"
        return [types.SimpleNamespace(text=w) for w in (" " + said).split(" ")[1:] for w in (" " + w,)]
    t._decode = decode
    return t


def test_listens_again_only_when_something_sounds_like_your_words(transcriber):
    assert transcriber.piece(np.zeros(16000), VOCAB) == "Foni works"
    assert transcriber.calls[-1] == "boosted"
    transcriber.calls.clear()
    assert transcriber.piece(np.zeros(16000), ("TikTok",)) == "phony works"   # nothing near: fast only
    assert all(c != "boosted" for c in transcriber.calls)


@pytest.fixture
def worker():
    """Foni's worker with a pretend microphone holding 70 s of talking, and a pretend
    transcriber that says how many seconds it was given."""
    rate = SAMPLE_RATE
    audio = np.random.default_rng(0).normal(0, 0.1, rate * 70).astype(np.float32)
    audio[rate * 22:rate * 23] = 0  # a pause at 22 s
    heard = []
    app = types.SimpleNamespace(
        press_id=5, ahead={"press": None, "done": 0, "texts": []}, cfg={"vocabulary": ["Foni"]},
        dictionary=types.SimpleNamespace(entries={}), last_heard=0.0, warmed=0.0,
        recorder=types.SimpleNamespace(seconds=lambda: len(audio) / rate, audio_so_far=lambda: audio),
        transcriber=types.SimpleNamespace(engine="parakeet", transcribe=lambda a, vocab=(): (
            heard.append(round(len(a) / rate)) or f"{round(len(a) / rate)}s")))
    for m in ("_transcribe_ahead", "_hear", "_your_words", "_keep_warm"):
        setattr(app, m, getattr(Foni, m).__get__(app))
    return app, audio, heard


def test_transcribes_while_you_talk_and_only_the_rest_at_the_end(worker):
    app, audio, heard = worker
    app._transcribe_ahead()
    assert 22 <= app.ahead["done"] / SAMPLE_RATE <= 23    # cut at the pause
    app._transcribe_ahead()                                # 47 s left: one more piece
    assert len(app.ahead["texts"]) == 2
    done = app.ahead["done"]
    assert app._hear(audio, app.ahead) == " ".join(app.ahead["texts"] + [f"{round((len(audio) - done) / SAMPLE_RATE)}s"])
    assert heard[-1] == round((len(audio) - done) / SAMPLE_RATE)  # only what was left


def test_a_new_recording_starts_fresh(worker):
    app, audio, _ = worker
    app._transcribe_ahead()
    app.press_id += 1
    app._transcribe_ahead()
    assert app.ahead["press"] == 6 and len(app.ahead["texts"]) == 1


def test_keeps_the_speech_model_warm_when_idle(worker):
    app, _, heard = worker
    app.last_heard = app.warmed = time.time()
    app._keep_warm()
    assert heard == []                       # just used: nothing to do
    app.last_heard = app.warmed = time.time() - 300
    app._keep_warm()
    assert heard == [1]                      # a one-second practice run


def test_your_words_include_learned_names(worker):
    app, _, _ = worker
    app.dictionary.entries = {"rabbits": {"to": "RAM", "count": 1}, "martown": {"to": "markdown", "count": 1},
                              "cloth": {"to": "Claude", "count": 2}}
    assert app._your_words() == ("Foni", "RAM", "Claude")


def test_short_words_are_not_favoured():
    # favouring "AI" turned "I want to" into "AI want to" in a real dictation
    assert boostable(("AI", "RAM", "UGC", "Foni", "Creator Studio")) == ["Foni", "Creator Studio"]
