"""Conversation notes: menu → Start conversation notes listens to a conversation (in the room, or
a call on your speakers) until you stop it, then saves the key points with the whole transcript
in Documents/Foni Notes, one file per conversation."""
import re
import time
from pathlib import Path

NOTES_DIR = Path.home() / "Documents" / "Foni Notes"


def title_of(notes):
    """The "# Title" line the AI wrote, made safe for a file name."""
    m = re.search(r"^#\s+(.+)$", notes or "", re.MULTILINE)
    title = m.group(1) if m else "Conversation"
    title = re.sub(r"[/:\\*?\"<>|]+", " ", title).strip(" .")
    return re.sub(r"\s+", " ", title)[:60] or "Conversation"


def save_notes(notes, transcript, started, minutes, folder=None):
    """Write the notes (or, without them, just the transcript) to a new file. Returns its path."""
    folder = Path(folder or NOTES_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    when = time.localtime(started)
    title = title_of(notes)
    body = (notes or "").strip() or f"# {title}\n\n(No AI was available to write notes; the full " \
                                     "transcript is below.)"
    stamp = time.strftime("%-d %b %Y, %H:%M", when)
    text = (f"{body}\n\n---\n\n*Recorded {stamp} · {max(1, round(minutes))} min*\n\n"
            f"## Transcript\n\n{transcript.strip()}\n")
    name = f"{time.strftime('%Y-%m-%d %H.%M', when)} {title}"
    path = folder / f"{name}.md"
    n = 2
    while path.exists():
        path = folder / f"{name} ({n}).md"
        n += 1
    path.write_text(text, encoding="utf-8")
    return path
