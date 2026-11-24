"""Hold to talk, and double tap for hands-free, fed with key timings recorded from a real keyboard."""
import queue
import types

import pytest
from Quartz import CGEventCreateKeyboardEvent, CGEventSetTimestamp

import foni.app as appmod
from foni.app import Foni, key_time, uptime
from foni.config import DEFAULT_CONFIG


@pytest.fixture
def foni(monkeypatch):
    """Foni's hotkey logic with a pretend microphone. `run([(time, "down"/"up"), ...])` plays key
    presses (and any timers they set) in order; it returns what got typed: one entry per dictation,
    (start, end) of the recording."""
    timers = []
    monkeypatch.setattr(appmod.threading, "Timer", lambda delay, fn, args=(): types.SimpleNamespace(
        start=lambda: timers.append([clock["now"] + delay, fn, args])))
    monkeypatch.setattr(appmod, "play", lambda name, cfg: None)
    monkeypatch.setattr(appmod, "frontmost_app", lambda: "Notes")
    monkeypatch.setattr(appmod, "print", lambda *a: None, raising=False)
    clock = {"now": 0.0}
    rec = {}
    app = types.SimpleNamespace(
        state="idle", cfg=dict(DEFAULT_CONFIG, tail_seconds=0), command=False, app="", rec_started=0,
        press_id=0, press_t=float("-inf"), last_tap=float("-inf"), latched_at=float("-inf"),
        swallow_release=False, jobs=queue.Queue(), events=queue.Queue(), _start_cue=lambda press_id: None,
        muter=types.SimpleNamespace(restore=lambda: None),
        recorder=types.SimpleNamespace(wedged=False, start=lambda: rec.update(start=clock["now"]),
                                       stop=lambda: (rec.get("start"), clock["now"])))
    for m in ("_on_hotkey", "_finish", "_stop_recording"):
        setattr(app, m, getattr(Foni, m).__get__(app))

    def run(keys):
        typed = []
        for t, kind in keys + [(keys[-1][0] + 5, None)]:
            for timer in sorted((x for x in timers if x[0] <= t), key=lambda x: x[0]):  # due first
                timers.remove(timer)
                clock["now"] = timer[0]
                timer[1](*timer[2])
                while not app.events.empty():
                    app._on_hotkey(*app.events.get())
            clock["now"] = t
            if kind:
                app._on_hotkey("press" if kind == "down" else "release", t, "left_control")
            while not app.jobs.empty():
                typed.append(app.jobs.get()[0])
                app.state = "idle"  # the worker has typed it
        return typed
    return app, run


def test_hold_to_talk(foni):
    app, run = foni
    assert run([(10.0, "down"), (14.0, "up")]) == [(10.0, 14.0)]


def test_quick_double_tap_starts_hands_free_and_a_tap_ends_it(foni):
    app, run = foni
    # recorded 17:04:53: taps of 29 ms and 90 ms, 315 ms apart; a tap 5.7 s later to finish
    typed = run([(875.200, "down"), (875.229, "up"), (875.544, "down"), (875.634, "up"),
                 (881.306, "down"), (881.410, "up")])
    assert typed == [(875.544, 881.306)] and app.state == "idle"


def test_a_lone_tap_types_nothing(foni):
    app, run = foni
    assert run([(5.0, "down"), (5.07, "up")]) == [] and app.state == "idle"


def test_slow_first_tap_still_starts_hands_free(foni):
    app, run = foni
    # the first tap is held 0.29 s (like at 17:09:01): it used to be taken as "hold to talk",
    # recording a fraction of a second, so the double tap never started hands-free
    run([(123.314, "down"), (123.599, "up"), (123.750, "down"), (123.810, "up")])
    assert app.state == "latched"


def test_two_slow_taps_start_hands_free(foni):
    app, run = foni
    run([(1.0, "down"), (1.3, "up"), (1.5, "down"), (1.8, "up")])
    assert app.state == "latched"


def test_a_short_word_held_briefly_is_still_typed(foni):
    app, run = foni
    # "Yes", held 0.3 s: waits a moment for a possible second tap, then it's typed
    assert run([(2.0, "down"), (2.3, "up")]) == [(2.0, 3.0)]


def test_a_bounce_right_after_starting_hands_free_is_ignored(foni):
    app, run = foni
    run([(1.0, "down"), (1.06, "up"), (1.2, "down"), (1.27, "up"),   # double tap
         (1.45, "down"), (1.50, "up")])                              # the key bounces
    assert app.state == "latched"
    assert run([(9.0, "down"), (9.1, "up")]) == [(1.2, 9.0)]        # a real tap ends it


def test_your_real_session_from_the_recorder(foni):
    """Every Control press between 17:03:59 and 17:09:47 today, as macOS timed them. Foni typed
    six hands-free dictations and one short "Also" (a tap 2.4 s into hands-free)."""
    app, run = foni
    keys = [(821.440, "down"), (821.486, "up"), (821.560, "down"), (821.651, "up"),
            (855.385, "down"), (855.434, "up"),
            (875.200, "down"), (875.229, "up"), (875.544, "down"), (875.634, "up"),
            (881.306, "down"), (881.410, "up"),
            (896.666, "down"), (896.745, "up"), (896.814, "down"), (896.935, "up"),
            (939.221, "down"), (939.285, "up"),
            (983.338, "down"), (983.410, "up"), (983.469, "down"), (983.532, "up"),
            (1075.375, "down"), (1075.542, "up"),
            (1123.314, "down"), (1123.599, "up"),                     # the slow 0.29 s press
            (1124.274, "down"), (1124.334, "up"), (1124.454, "down"), (1124.529, "up"),
            (1126.904, "down"), (1127.021, "up"),
            (1128.490, "down"), (1128.549, "up"), (1128.625, "down"), (1128.700, "up"),
            (1169.425, "down"), (1169.518, "up")]
    lengths = [end - start for start, end in run(keys)]
    # 34, 6, 42 and 92 s as typed today; then the slow press followed by one quick tap is
    # dropped (it used to type an empty 0.4 s recording); then "Also" and the 41 s one
    assert lengths == pytest.approx([33.825, 5.762, 42.407, 91.906, 2.45, 40.8], abs=0.001)


def test_key_time_uses_the_keyboards_clock():
    e = CGEventCreateKeyboardEvent(None, 59, True)
    pressed = uptime() - 0.4                   # pressed 0.4 s before Foni got to it
    CGEventSetTimestamp(e, int(pressed * 1e9))
    assert abs(key_time(e) - pressed) < 0.01
    CGEventSetTimestamp(e, 0)                  # no usable stamp: use the time it arrived
    assert abs(key_time(e) - uptime()) < 0.01
