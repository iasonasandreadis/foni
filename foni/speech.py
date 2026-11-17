"""Speech to text, on this Mac (Parakeet, or Whisper) with MLX on the Apple GPU."""
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


class Transcriber:
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
        else:
            import mlx_whisper
            self.mlx_whisper = mlx_whisper
            self.whisper_path = model_path(cfg["whisper_model"], on_progress)
        # warm-up: loads the weights and compiles the GPU kernels before your first dictation
        self.transcribe(np.random.default_rng(0).normal(0, 0.01, SAMPLE_RATE * 2).astype(np.float32))
        print(f"[model] {self.engine} ready in {time.time() - t:.1f}s")

    def transcribe(self, audio):
        pad = np.zeros(int(SAMPLE_RATE * 0.15), dtype=np.float32)  # clean edges help the model
        audio = np.concatenate([pad, audio.astype(np.float32), pad])
        if self.engine == "parakeet":
            return self._parakeet(audio)
        return self._whisper(audio)["text"].strip()

    def _parakeet(self, audio):
        if len(audio) > SAMPLE_RATE * 120:  # over 2 minutes: in pieces, cut where you paused
            texts = [self._parakeet_piece(piece) for piece in split_at_pauses(audio)]
            return " ".join(t for t in texts if t)
        return self._parakeet_piece(audio)

    def _parakeet_piece(self, audio):
        x = self.mx.array(audio, dtype=self.mx.float32)
        mel = self.get_logmel(x, self.model.preprocessor_config)
        return self.model.generate(mel)[0].text.strip()

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
