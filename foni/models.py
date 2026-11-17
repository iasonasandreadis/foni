"""Finding and downloading the AI models (from Hugging Face, once; after that, offline)."""
import threading
from pathlib import Path


def is_downloaded(repo):
    from huggingface_hub import snapshot_download
    try:
        snapshot_download(repo, local_files_only=True)
        return True
    except Exception:
        return False


def model_path(repo, on_progress=None):
    """The model's folder on this Mac. Uses the downloaded copy without going online; downloads
    it the first time, calling on_progress(bytes_done, bytes_total or None) while it does."""
    from huggingface_hub import constants, snapshot_download
    try:
        return snapshot_download(repo, local_files_only=True)
    except Exception:
        pass
    print(f"[model] downloading {repo}")
    total = None
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo, files_metadata=True)
        total = sum(s.size or 0 for s in info.siblings) or None
    except Exception:
        pass
    blobs = Path(constants.HF_HUB_CACHE) / ("models--" + repo.replace("/", "--")) / "blobs"
    done = threading.Event()

    def watch():
        while not done.wait(0.5):
            try:
                got = sum(f.stat().st_size for f in blobs.iterdir() if f.is_file())
            except OSError:
                got = 0
            on_progress(got, total)

    if on_progress:
        threading.Thread(target=watch, daemon=True).start()
    try:
        return snapshot_download(repo)
    finally:
        done.set()
