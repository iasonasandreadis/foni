"""Settings and where Foni keeps its files (~/.foni)."""
import json
import shutil
from pathlib import Path

APP_DIR = Path.home() / ".foni"
CONFIG_PATH = APP_DIR / "config.json"
HISTORY_PATH = APP_DIR / "history.jsonl"
LEARNED_PATH = APP_DIR / "dictionary.json"  # words learned from your corrections
SPEND_PATH = APP_DIR / "claude_spend.json"   # what Claude has charged, per day
LOG_PATH = APP_DIR / "foni.log"
OLD_APP_DIR = Path.home() / ".localflow"     # Foni's name during development

DEFAULT_CONFIG = {
    # One key or a list; any of them works: fn, right_option, right_command, right_control,
    # right_shift, left_option, left_command, left_control, left_shift.
    # Easiest: menu bar → Hotkeys → "Add a hotkey…" and press the key.
    "hotkey": ["fn"],
    # "parakeet": ~0.3s per 10s of speech, 25 European languages (en, el, de, fr, es, ...).
    # "whisper":  ~2s per 10s of speech, 99 languages.
    "engine": "parakeet",
    "parakeet_model": "mlx-community/parakeet-tdt-0.6b-v3",
    "whisper_model": "mlx-community/whisper-large-v3-turbo",
    "language": None,  # Whisper only: null = auto-detect, or e.g. "en", "el"
    # AI clean-up: "smart" polishes only messy dictation (self-corrections, lists, repeated
    # words, fillers, long dictation) so clean sentences stay instant; "always"; or "off".
    "ai_editing": "smart",
    # Where the AI runs:
    #   "local"  - on this Mac: private, offline, free (downloads a ~2 GB model on first use)
    #   "cloud"  - Claude, with your own API key (menu → AI runs on → Set Claude API key…)
    #   "openai" - any OpenAI-style service: OpenAI, OpenRouter, Groq, or a local Ollama /
    #              LM Studio server (menu → AI runs on → Set up OpenAI-style service…)
    "ai_engine": "local",
    # Mac model. 3B: ~0.5s, ~2 GB. 7B: ~1s, ~4 GB, a little more faithful.
    "ai_model": "mlx-community/Qwen2.5-3B-Instruct-4bit",
    # Optional tiny model that drafts ahead for the big one (same output). null = draft by
    # looking ahead in your own transcript instead, which is as fast and uses less memory.
    "ai_draft_model": None,
    "cloud_model": "claude-haiku-5-5",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "",
    # Names and terms that should always be spelled your way.
    "vocabulary": [],
    # Say the left side, get the right side.
    "replacements": {"new line": "\n", "new paragraph": "\n\n"},
    # Say the trigger, get the whole text, e.g. {"my email": "me@example.com"}.
    "snippets": {},
    "remove_fillers": True,
    "sounds": True,
    "mute_while_dictating": True,  # silence the Mac while you talk, so the mic hears only you
    "show_pill": True,
    "show_idle_pill": True,  # small resting pill at the bottom of the screen
    "pill_style": "fire",    # fire, bubbles, stardust, hearts, minimal, rainbow, equalizer
    # How dictation is written in each kind of app (menu → Writing style): "formal" (as said,
    # with full stops), "casual" (no full stop at the end), "very_casual" (no capitals either,
    # personal messages only) or "excited" (ends with "!"). Code editors and AI chats never change.
    "app_styles": {"personal": "casual", "work": "formal", "email": "formal", "ai": "casual",
                   "other": "formal"},
    # keep listening this long after you let go, so the last word isn't cut off
    "tail_seconds": 0.25,
    # The Mac AI model is only loaded when a dictation needs it, and freed after this many
    # minutes without use (0 = keep it loaded once it's been used).
    "ai_unload_after_minutes": 3,
    "min_hold_seconds": 0.25,
    "double_tap_window": 0.45,
    # Once a day, ask GitHub whether there's a newer Foni (menu → Check for updates…).
    "check_updates": True,
    "welcomed": False,     # the first-launch welcome has been shown
    "login_asked": False,  # Foni has turned on "Open at login" once (you can turn it off)
}


def migrate_old_data():
    """Bring settings, dictionary, history and spend over from LocalFlow (Foni's old name)."""
    if CONFIG_PATH.exists() or not OLD_APP_DIR.is_dir():
        return False
    APP_DIR.mkdir(exist_ok=True)  # may already hold the launcher's log
    for name in ("config.json", "dictionary.json", "history.jsonl", "claude_spend.json"):
        if (OLD_APP_DIR / name).exists():
            shutil.copy2(OLD_APP_DIR / name, APP_DIR / name)
    if CONFIG_PATH.exists():  # someone moving over has already been welcomed
        cfg = json.loads(CONFIG_PATH.read_text())
        cfg["welcomed"] = True
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return True


def load_config():
    APP_DIR.mkdir(exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            on_disk = json.loads(CONFIG_PATH.read_text())
            cfg.update({k: v for k, v in on_disk.items() if k in DEFAULT_CONFIG})
            # a kind of app added in a newer Foni gets its default style
            cfg["app_styles"] = {**DEFAULT_CONFIG["app_styles"], **(cfg.get("app_styles") or {})}
        except Exception as e:
            print(f"[config] could not parse {CONFIG_PATH}: {e}")
            return cfg
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
