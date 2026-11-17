import collections
import math
import numpy as np
import sounddevice as sd


SAMPLE_RATE = 16000
BLOCK = 640  # 40 ms of audio per level reading
BARS = 18

# ---------------------------------------------------------------- audio

class Recorder:
    def __init__(self):
        self.chunks = []
        self.stream = None
        self.levels = collections.deque([0.0] * BARS, maxlen=BARS)

    def _callback(self, data, frames, t, status):
        self.chunks.append(data.copy())
        rms = float(np.sqrt(np.mean(data ** 2)))
        db = 20 * math.log10(rms + 1e-7)
        self.levels.append(min(1.0, max(0.0, (db + 52) / 38)))

    def start(self):
        self.chunks = []
        self.levels.extend([0.0] * BARS)
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                     blocksize=BLOCK, callback=self._callback)
        self.stream.start()

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self.chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.chunks)[:, 0]


def has_speech(audio):
    """Is anyone talking? Compares the loudest moments with the room's background level,
    so whispering in a quiet room counts, but silence or steady noise doesn't."""
    n = len(audio) // 320  # 20 ms frames
    if n < 5:
        return False
    e = np.sqrt(np.mean(audio[:n * 320].reshape(n, 320) ** 2, axis=1))
    noise, voice = np.percentile(e, 10), np.percentile(e, 95)
    return voice > 0.0003 and voice > noise * 1.8
