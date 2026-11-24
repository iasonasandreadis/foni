import collections
import math
import threading
import time
import numpy as np
import sounddevice as sd


SAMPLE_RATE = 16000
BLOCK = 640  # 40 ms of audio per level reading
BARS = 18

# ---------------------------------------------------------------- audio

class Recorder:
    # macOS can freeze for good when a microphone is stopped while it's still starting up: two
    # of its audio threads end up waiting on each other, and Foni stayed "recording" with your
    # sound muted. A quick tap or Ctrl+key stops it a few milliseconds after it starts, so:
    MIN_AGE = 0.3    # never stop the microphone sooner than this after starting it
    STOP_WAIT = 2.0  # and if stopping still hangs, give up on it after this long ("wedged")

    def __init__(self):
        self.chunks = []
        self.stream = None
        self.levels = collections.deque([0.0] * BARS, maxlen=BARS)
        self.started = 0.0
        self.flowing = threading.Event()  # the first audio has arrived
        self.wedged = False               # macOS's audio froze; only a restart frees the mic

    def _callback(self, data, frames, t, status):
        self.chunks.append(data.copy())
        rms = float(np.sqrt(np.mean(data ** 2)))
        db = 20 * math.log10(rms + 1e-7)
        self.levels.append(min(1.0, max(0.0, (db + 52) / 38)))
        self.flowing.set()

    def start(self):
        if self.wedged:
            raise RuntimeError("the microphone is stuck in macOS; Foni is restarting to free it")
        self.chunks = []
        self.flowing.clear()
        self.levels.extend([0.0] * BARS)
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                     blocksize=BLOCK, callback=self._callback)
        self.started = time.monotonic()
        self.stream.start()

    def stop(self):
        stream, self.stream = self.stream, None
        if stream:
            self.flowing.wait(self.MIN_AGE)  # let it finish starting up before stopping it
            time.sleep(max(0.0, self.started + self.MIN_AGE - time.monotonic()))
            done = threading.Event()

            def close():
                try:
                    stream.stop()
                    stream.close()
                except Exception as e:
                    print(f"[mic] {e}")
                done.set()
            threading.Thread(target=close, daemon=True).start()
            if not done.wait(self.STOP_WAIT):
                self.wedged = True
                print("[mic] macOS's audio froze while stopping the microphone; "
                      "Foni restarts once this dictation is typed")
        chunks = list(self.chunks)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)[:, 0]


def has_speech(audio):
    """Is anyone talking? Compares the loudest moments with the room's background level,
    so whispering in a quiet room counts, but silence or steady noise doesn't."""
    n = len(audio) // 320  # 20 ms frames
    if n < 5:
        return False
    e = np.sqrt(np.mean(audio[:n * 320].reshape(n, 320) ** 2, axis=1))
    noise, voice = np.percentile(e, 10), np.percentile(e, 95)
    return voice > 0.0003 and voice > noise * 1.8
