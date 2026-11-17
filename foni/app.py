"""Foni's menu bar app: the hotkey, recording, the pill, and the menu."""
import json
import math
import queue
import rumps
import subprocess
import threading
import time
from AppKit import NSApplication, NSBundle, NSImage, NSPasteboard, NSPasteboardTypeString
from CoreFoundation import CFEqual
from Quartz import (
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetMain,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventMaskBit,
    CGEventSourceFlagsState,
    CGEventTapCreate,
    CGEventTapEnable,
    CGPreflightListenEventAccess,
    CGPreflightPostEventAccess,
    kCFRunLoopCommonModes,
    kCGEventFlagsChanged,
    kCGEventKeyDown,
    kCGEventSourceStateHIDSystemState,
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
    kCGEventTapOptionListenOnly,
    kCGHeadInsertEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)
from datetime import date, datetime
from . import __version__
from .ai_cloud import (
    CLAUDE_KEY,
    OPENAI_KEY,
    ClaudeSpend,
    CloudEditor,
    OpenAIEditor,
    fmt_usd,
    keychain_get,
    keychain_set,
    migrate_old_key,
)
from .ai_local import AIEditor
from .audio import Recorder, SAMPLE_RATE, has_speech
from .bubble import CopyBubble
from .config import (
    CONFIG_PATH,
    HISTORY_PATH,
    LEARNED_PATH,
    load_config,
    save_config,
)
from .hotkeys import (
    FN,
    HOTKEYS,
    KEYCODE_TO_NAME,
    MODIFIER_NAMES,
    command_modifier,
    pretty,
)
from .learning import Dictionary, _words, find_corrections
from .models import is_downloaded
from .pills import PILL_STYLES, STYLE_ACCENTS
from .setup_window import SetupWindow
from . import updates
from .speech import Transcriber
from .system import (
    element_text,
    focused_element,
    focused_text_target,
    frontmost_app,
    grab_selection,
    play,
    restore_clipboard,
    type_text,
    wait_for_modifiers_released,
)
from .text import (
    STYLE_CHOICES,
    STYLE_GROUPS,
    WRITING_STYLES,
    apply_style,
    postprocess,
    style_group,
)


# ---------------------------------------------------------------- stats

class Stats:
    def __init__(self):
        self.today = date.today().isoformat()
        self.words_today = 0
        self.words = 0
        self.seconds = 0.0
        if HISTORY_PATH.exists():
            for line in HISTORY_PATH.read_text().splitlines():
                try:
                    self.add(json.loads(line))
                except Exception:
                    pass

    def add(self, rec):
        n = len(rec.get("text", "").split())
        self.words += n
        self.seconds += rec.get("audio_s", 0)
        if datetime.fromtimestamp(rec.get("t", 0)).date().isoformat() == self.today:
            self.words_today += n

    def label(self):
        wpm = self.words / (self.seconds / 60) if self.seconds > 5 else 0
        return f"Today: {self.words_today:,} words · Avg {wpm:.0f} wpm"


# ---------------------------------------------------------------- app

class Foni(rumps.App):
    # (setting value, menu label, menu bar icon); None = a divider in the menu
    STYLES = (("fire", "Fire", "flame"),
              ("bubbles", "Bubbles", "bubbles.and.sparkles"),
              ("stardust", "Stardust", "sparkles"),
              ("hearts", "Hearts", "heart"),
              None,
              ("minimal", "Minimal (white waveform)", "waveform"),
              ("rainbow", "Rainbow", "rainbow"),
              ("equalizer", "Retro equalizer", "chart.bar.fill"))
    # (setting value, menu label, short name shown in the menu's title)
    AI_MODES = (("smart", "Only messy dictation (recommended)", "Only messy dictation"),
                ("always", "Every dictation", "Every dictation"),
                ("off", "Never", "Off"))
    AI_ENGINES = (("local", "This Mac (private, offline, free)", "This Mac"),
                  ("cloud", "Claude (your API key)", "Claude"),
                  ("openai", "OpenAI-style service (OpenAI, OpenRouter, Groq, Ollama…)", "OpenAI-style"))
    AI_MODELS = (("mlx-community/Qwen2.5-3B-Instruct-4bit", "Fast (3B, ~2 GB)", "Fast (3B)"),
                 ("mlx-community/Qwen2.5-7B-Instruct-4bit", "Accurate (7B, ~4 GB)", "Accurate (7B)"))
    REPO = "https://github.com/iasonasandreadis/foni"

    def __init__(self):
        super().__init__("Foni", title="…", quit_button=None)
        # One quiet monochrome icon in the menu bar that never changes while you talk;
        # the floating pill is the only live indicator.
        self.cfg = load_config()
        icon = self._style_icon()
        if icon is not None:
            self._icon_nsimage = icon
        self.cfg_mtime = CONFIG_PATH.stat().st_mtime
        hk = self.cfg["hotkey"]
        self.hotkeys = [h for h in ([hk] if isinstance(hk, str) else hk) if h in HOTKEYS] or ["fn"]
        self.keys_down = set()
        self.learning = False   # waiting for the user to press a new hotkey
        self.learned = None
        self.state = "loading"
        self.command = False    # current recording is a voice command
        self.app = ""           # app that had focus when recording started
        self.last_text = ""
        self.recorder = Recorder()
        self.events = queue.Queue()
        self.jobs = queue.Queue()
        self.transcriber = None
        self.editor = None            # AI editor: loaded on demand, freed when idle
        self.cloud = None             # Claude or OpenAI-style editor (when the AI runs in the cloud)
        self.cloud_dirty = False
        self.spend = ClaudeSpend()    # what Claude has charged so far (menu → Claude spend)
        self.spend_shown = -1
        self.editor_lock = threading.Lock()
        self.editor_loading = False
        self.editor_ready = threading.Event()
        self.editor_ready.set()
        self.editor_warmed = False
        self.ai_last_used = time.time()
        self.stats = Stats()
        self.stats_dirty = False
        self.pills = {}           # each style's window is made the first time it's used
        self.pill = self._get_pill(self.cfg["pill_style"])
        self.copy_bubble = CopyBubble(anchor=self._pill_anchor)
        self.copy_bubble.set_accent(STYLE_ACCENTS.get(self.cfg["pill_style"]))
        self.rec_started = 0.0    # when the current recording began
        self.press_id = 0         # identifies a key press, for the delayed start sound
        self.preview_until = 0.0  # Look → "Preview" in the menu
        self.setup_status = None  # "↓ 42%" in the menu bar while a model downloads
        self.welcome_pending = True   # first tick: open the setup window if anything's missing
        self.setup = None             # the setup window, made when first needed
        self.setup_refreshed = 0.0
        self.update = None            # (version, notes, dmg_url, page_url) when a newer Foni exists
        self.update_shown = None
        self.update_checked = time.time() - 86400 + 60  # first check a minute after launch
        self.restart_pending = False
        self.update_dialog_pending = False  # "Check for updates…" found one: show it right away
        self.pending_copy = None  # text to offer in the Copy bubble (set by the worker)
        self.pending_toast = None
        self.dictionary = Dictionary()
        self.watch_token = 0      # bumps on every paste; stops the previous edit watcher

        self.command_mod = command_modifier(self.hotkeys)
        self.hint_item = rumps.MenuItem(self._hint())
        self.command_hint_item = rumps.MenuItem(self._command_hint())
        self.stats_item = rumps.MenuItem(self.stats.label())
        self._build_ai_menus()
        self._build_writing_menu()
        self.pill_item = rumps.MenuItem("Show pill while dictating", callback=self.toggle_pill)
        self.pill_item.state = self.cfg["show_pill"]
        self.idle_pill_item = rumps.MenuItem("Show small pill when idle",
                                             callback=self.toggle_idle_pill)
        self.idle_pill_item.state = self.cfg["show_idle_pill"]
        self.style_menu = rumps.MenuItem("Look")
        for style in self.STYLES:
            if style is None:
                self.style_menu.add(None)
                continue
            key, label, _ = style
            item = rumps.MenuItem(label, callback=self.set_style)
            item.lf_key = key
            item.state = self.cfg["pill_style"] == key
            self.style_menu.add(item)
        self.style_menu.add(None)
        self.style_menu.add(self.pill_item)
        self.style_menu.add(self.idle_pill_item)
        self.style_menu.add(rumps.MenuItem("Preview", callback=self.start_preview))
        self.hotkey_menu = rumps.MenuItem("Hotkeys")
        self.hotkey_menu.add(rumps.MenuItem("Add a hotkey… (then press the key)", callback=self.start_learning))
        self.hotkey_menu.add(rumps.MenuItem("Reset to Fn only", callback=self.reset_hotkeys))
        self.login_item = rumps.MenuItem("Open at login", callback=self.toggle_login)
        self.login_item.state = self._login_enabled()
        self.perm_ok = self._check_permissions()
        self.perm_item = rumps.MenuItem("⚠︎ Needs permission: finish setting up…",
                                        callback=self.show_setup)
        self.update_item = rumps.MenuItem("Update available…", callback=self.show_update)
        self.menu = [
            self.update_item,
            self.perm_item,
            self.hint_item,
            self.command_hint_item,
            self.stats_item,
            None,
            self.mode_menu,
            self.engine_menu,
            self.model_menu,
            self.spend_menu,
            self.writing_menu,
            self.style_menu,
            self.hotkey_menu,
            None,
            rumps.MenuItem("Copy last dictation", callback=self.copy_last),
            self._dictionary_item(),
            rumps.MenuItem("Edit snippets & settings…", callback=self.open_config),
            self.login_item,
            None,
            rumps.MenuItem("Setup & permissions…", callback=self.show_setup),
            rumps.MenuItem("Check for updates…", callback=self.check_updates_now),
            rumps.MenuItem("About Foni…", callback=self.about),
            rumps.MenuItem("Restart Foni", callback=self.restart),
            rumps.MenuItem("Quit Foni", callback=lambda _: rumps.quit_application()),
        ]
        self.perm_item._menuitem.setHidden_(self.perm_ok)
        self.update_item._menuitem.setHidden_(True)
        if self._bundle() is None:  # running from source: there's no app to open at login
            self.login_item._menuitem.setHidden_(True)
        threading.Thread(target=migrate_old_key, daemon=True).start()

        threading.Thread(target=self._controller, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()
        self._install_tap()
        self.title_shown = None
        # Cheap 10 Hz check while idle; the 30 fps pill animation only runs while it's on screen.
        self.anim_timer = rumps.Timer(self._animate, 1 / 30)
        self.anim_running = False
        rumps.Timer(self._tick, 0.1).start()

    # ---- UI (main thread)
    def _pill_wanted(self):
        if time.time() < self.preview_until:
            return True
        if not self.cfg["show_pill"] or self.state not in ("recording", "latched", "working"):
            return False
        # a quick shortcut (Ctrl+C with Control as a hotkey) shouldn't flash the fire
        return self.state != "recording" or time.time() - self.rec_started > 0.18

    def _pill_anchor(self):
        return self.pill.anchor()

    PREVIEW = (("recording", 3.0), ("latched", 1.6), ("command", 1.6), ("working", 1.4))

    def start_preview(self, _=None):
        self.preview_until = time.time() + sum(d for _, d in self.PREVIEW)

    def _preview_mode(self, now):
        t = now - (self.preview_until - sum(d for _, d in self.PREVIEW))
        for mode, d in self.PREVIEW:
            if t < d:
                return mode
            t -= d
        return "working"

    def _animate(self, _):
        if self._pill_wanted():
            if self.copy_bubble.visible:
                self.copy_bubble.hide()
            now = time.time()
            if now < self.preview_until:  # a pretend voice
                mode = self._preview_mode(now)
                speech = 0.55 + 0.3 * math.sin(now * 2.3) + 0.15 * math.sin(now * 9.1)
                self.pill.show(mode, [max(0.0, min(1.0, speech))] * 3)
                return
            mode = self.state
            if self.command and mode != "working":
                mode = "command"
            self.pill.show(mode, list(self.recorder.levels))
        else:
            self.anim_timer.stop()
            self.anim_running = False
            self._rest_pill()

    def _rest_pill(self):
        if self.copy_bubble.visible:
            self.pill.show("none")  # the Copy button takes the ember's place
        elif self.cfg["show_idle_pill"] and self.state != "loading":
            self.pill.show("idle")
        else:
            self.pill.hide()

    def _tick(self, _):
        self._check_stuck_keys()
        if self.welcome_pending:
            self.welcome_pending = False
            if not self.cfg.get("welcomed") or not self.perm_ok:
                self.show_setup()
            if not self.cfg.get("welcomed"):
                self.cfg["welcomed"] = True
                save_config(self.cfg)
        if self.restart_pending:
            self.restart_pending = False
            self.restart()
        if self.cfg.get("check_updates", True) and time.time() - self.update_checked > 86400:
            self.update_checked = time.time()
            threading.Thread(target=self._check_updates, daemon=True).start()
        if self.update_dialog_pending and self.update:
            self.update_dialog_pending = False
            self.update_shown = self.update[0]
            self.update_item.title = f"⬆︎ Foni {self.update[0]} is available: install…"
            self.update_item._menuitem.setHidden_(False)
            self.show_update()
        if self.update and self.update_shown != self.update[0]:
            self.update_shown = self.update[0]
            self.update_item.title = f"⬆︎ Foni {self.update[0]} is available: install…"
            self.update_item._menuitem.setHidden_(False)
            self.copy_bubble.toast(f"Foni {self.update[0]} is available (see the menu)", seconds=6)
        if self.setup is not None and self.setup.visible and time.time() - self.setup_refreshed > 0.5:
            self.setup_refreshed = time.time()
            self.setup.refresh()
        if not self.cfg.get("login_asked") and self._bundle():
            self.cfg["login_asked"] = True  # once: open at login (Foni lives in the menu bar)
            save_config(self.cfg)
            self._set_login(True)
        if not self.perm_ok and time.time() - getattr(self, "perm_checked", 0) > 2:
            self._recheck_permissions()
        if self._pill_wanted():
            if not self.anim_running:
                self.anim_running = True
                self.anim_timer.start()
        elif not self.anim_running:
            self._rest_pill()
        if self.pending_copy is not None:
            text, self.pending_copy = self.pending_copy, None
            self.copy_bubble.show(text)
        if self.writing_dirty:
            self.writing_dirty = False
            self._refresh_writing_menu()
        if self.cloud_dirty:
            self.cloud_dirty = False
            self._refresh_ai_menus()
        elif self.spend.changes != self.spend_shown:
            self._refresh_spend()  # a Claude reply came in on the worker thread
        if self.pending_toast is not None:
            label, self.pending_toast = self.pending_toast, None
            self.copy_bubble.toast(label)
            self.dict_item.title = self._dictionary_label()
        self.copy_bubble.tick()
        if self.learned:
            name, self.learned = self.learned, None
            if name not in self.hotkeys:
                self.hotkeys.append(name)
            self._save_hotkeys()
        if self.stats_dirty:
            self.stats_dirty = False
            self.stats_item.title = self.stats.label()
        title = ("Press a key…" if self.learning else self.setup_status
                 or ("…" if self.state == "loading" else None))
        if self.title_shown != title:
            self.title_shown = title
            self.title = title

    def _hint(self):
        return f"Hold {pretty(self.hotkeys)} to dictate"

    def _command_hint(self):
        return f"Hold it + {MODIFIER_NAMES[self.command_mod]} to ask the AI to write or rewrite"

    def start_learning(self, _):
        self.learning = not self.learning  # clicking again cancels

    def reset_hotkeys(self, _):
        self.hotkeys = ["fn"]
        self._save_hotkeys()

    def _save_hotkeys(self):
        self.cfg["hotkey"] = list(self.hotkeys)
        save_config(self.cfg)
        self.command_mod = command_modifier(self.hotkeys)
        self.hint_item.title = self._hint()
        self.command_hint_item.title = self._command_hint()
        print(f"[hotkey] {self._hint()} · {self._command_hint()}")

    def _build_ai_menus(self):
        """Three separate choices, each its own menu with exactly one tick; the menu's title
        shows the current choice ("AI runs on: Claude Haiku (cloud)")."""
        def choice_menu(options, callback):
            menu, items = rumps.MenuItem("…"), {}
            for key, label, _ in options:
                item = rumps.MenuItem(label, callback=callback)
                item.lf_key = key
                items[key] = item
                menu.add(item)
            return menu, items
        self.mode_menu, self.mode_items = choice_menu(self.AI_MODES, self.set_ai_mode)
        self.engine_menu, self.engine_items = choice_menu(self.AI_ENGINES, self.set_engine)
        self.engine_menu.add(None)
        self.engine_menu.add(rumps.MenuItem("Set Claude API key…", callback=self.set_api_key))
        self.engine_menu.add(rumps.MenuItem("Set up OpenAI-style service…", callback=self.set_openai))
        self.model_menu, self.model_items = choice_menu(self.AI_MODELS, self.set_ai_model)
        self.spend_menu = rumps.MenuItem("Claude spend: …")
        # (rumps keys submenu items by their first title, so each needs its own)
        self.spend_lines = [rumps.MenuItem(f"spend line {i}") for i in range(4)]
        for line in self.spend_lines:
            self.spend_menu.add(line)
        self.spend_note = rumps.MenuItem("spend note")
        self.spend_menu.add(self.spend_note)
        self.spend_menu.add(None)
        self.spend_menu.add(rumps.MenuItem("Reset spend counter…", callback=self.reset_spend))
        self._refresh_ai_menus()

    def _refresh_ai_menus(self):
        cloud = self.cfg["ai_engine"] != "local"
        short = lambda options, value: next((s for k, _, s in options if k == value), value)
        for key, item in self.mode_items.items():
            item.state = key == self.cfg["ai_editing"]
        for key, item in self.engine_items.items():
            item.state = key == self.cfg["ai_engine"]
        for key, item in self.model_items.items():
            item.state = key == self.cfg["ai_model"]
        self.mode_menu.title = f"AI clean-up: {short(self.AI_MODES, self.cfg['ai_editing'])}"
        self.engine_menu.title = f"AI runs on: {short(self.AI_ENGINES, self.cfg['ai_engine'])}"
        self.model_menu.title = (("Offline backup: " if cloud else "Mac model: ")
                                 + short(self.AI_MODELS, self.cfg["ai_model"]))
        self._refresh_spend()

    def _refresh_spend(self):
        """'Claude spend: $0.0012 so far' with today / month / pace inside. Shown while Claude
        is on, or once it has cost anything."""
        self.spend_shown = self.spend.changes
        s = self.spend.summary()
        usd, calls = s["all"]
        plural = lambda n: f"{n} edit" + ("" if n == 1 else "s")
        self.spend_menu.title = f"Claude spend: {fmt_usd(usd)} so far"
        since = (date.fromisoformat(s["since"]).strftime("%b %-d") if s["since"] else "now")
        self.spend_lines[0].title = f"Today: {fmt_usd(s['today'][0])} · {plural(s['today'][1])}"
        self.spend_lines[1].title = f"This month: {fmt_usd(s['month'][0])} · {plural(s['month'][1])}"
        self.spend_lines[2].title = f"All time: {fmt_usd(usd)} · {plural(calls)} since {since}"
        self.spend_lines[3].title = (f"At this pace: about {fmt_usd(s['per_month'])} a month"
                                     if calls else "At this pace: nothing yet")
        before = s["before"]
        self.spend_note.title = (f"Includes ~{fmt_usd(before.get('usd', 0))} estimated "
                                 "from before tracking" if before else "")
        self.spend_note._menuitem.setHidden_(not before)
        show = self.cfg["ai_engine"] == "cloud" or (usd > 0 and self.cfg["ai_engine"] != "openai")
        self.spend_menu._menuitem.setHidden_(not show)

    def reset_spend(self, _):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if rumps.alert("Reset the Claude spend counter?",
                       "This only clears Foni's count. It doesn't change your Claude bill.",
                       ok="Reset", cancel="Cancel") == 1:
            self.spend.reset()
            self._refresh_spend()

    # ---- writing style per app (Wispr-like Styles, done with text rules)
    def _build_writing_menu(self):
        """Writing style ▸ Personal messages: Casual ▸ (Formal / Casual / Very casual) etc.
        One tick per kind of app; each title shows the current choice."""
        self.writing_dirty = False
        self.writing_menu = rumps.MenuItem("Writing style per app")
        self.writing_items = {}
        for group, label, _ in STYLE_GROUPS:
            sub = rumps.MenuItem(f"{label}: …")
            items = {}
            for key in STYLE_CHOICES.get(group, ("formal", "casual", "excited")):
                item = rumps.MenuItem(WRITING_STYLES[key][1], callback=self.set_writing_style)
                item.lf_group, item.lf_key = group, key
                items[key] = item
                sub.add(item)
            self.writing_items[group] = (sub, label, items)
            self.writing_menu.add(sub)
        self.writing_menu.add(None)
        self.writing_menu.add(rumps.MenuItem("Code editors and AI chats always stay as you said them"))
        self._refresh_writing_menu()

    def _refresh_writing_menu(self):
        styles = self.cfg.get("app_styles") or {}
        for group, (sub, label, items) in self.writing_items.items():
            current = styles.get(group, "formal")
            for key, item in items.items():
                item.state = key == current
            sub.title = f"{label}: {WRITING_STYLES.get(current, WRITING_STYLES['formal'])[0]}"

    def set_writing_style(self, item):
        styles = dict(self.cfg.get("app_styles") or {})
        styles[item.lf_group] = item.lf_key
        self.cfg["app_styles"] = styles
        save_config(self.cfg)
        self._refresh_writing_menu()

    def set_ai_mode(self, item):
        self.cfg["ai_editing"] = item.lf_key
        save_config(self.cfg)
        self._refresh_ai_menus()

    def set_ai_model(self, item):
        if item.lf_key != self.cfg["ai_model"]:
            self.cfg["ai_model"] = item.lf_key
            save_config(self.cfg)
            self.jobs.put(None)  # tells the worker thread to swap models
        self._refresh_ai_menus()

    def set_engine(self, item):
        if item.lf_key == "cloud" and not keychain_get(CLAUDE_KEY):
            self.set_api_key()  # needs a key first; switches to Claude once the key checks out
            return
        if item.lf_key == "openai" and not self.cfg.get("openai_model"):
            self.set_openai()  # needs a service first; switches once it answers
            return
        self.cfg["ai_engine"] = item.lf_key
        save_config(self.cfg)
        self.cloud = None
        self._refresh_ai_menus()
        print(f"[cloud] the AI now runs on: {item.lf_key}")

    def toggle_pill(self, item):
        item.state = not item.state
        self.cfg["show_pill"] = bool(item.state)
        save_config(self.cfg)

    # ---- cloud AI editing (Claude Haiku)
    def _get_ai(self):
        """The editor for this dictation: the cloud service you set up, else the Mac model."""
        engine = self.cfg.get("ai_engine")
        if engine in ("cloud", "openai") and self.cloud is None:
            if engine == "cloud":
                key = keychain_get(CLAUDE_KEY)
                if key:
                    self.cloud = CloudEditor(key, self.cfg["cloud_model"], self._get_editor,
                                             meter=self.spend)
                else:
                    print("[cloud] no Claude key saved; using the Mac model")
            elif self.cfg.get("openai_model"):
                self.cloud = OpenAIEditor(self.cfg["openai_base_url"], self.cfg["openai_model"],
                                          keychain_get(OPENAI_KEY), self._get_editor)
        if engine in ("cloud", "openai") and self.cloud is not None:
            return self.cloud
        return self._get_editor()

    def set_api_key(self, _=None):
        """You paste the key; it goes straight into the Keychain, then gets a quick test."""
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        r = rumps.Window(
            title="Claude API key",
            message=("Paste your Claude API key. Foni keeps it in your Mac's Keychain. "
                     "Only the text of dictations that need AI editing is sent to Claude, "
                     "never your audio."),
            default_text="", ok="Save", cancel="Cancel", dimensions=(360, 24), secure=True).run()
        key = (r.text or "").strip()
        if not r.clicked or not key:
            return
        if not keychain_set(key, CLAUDE_KEY):
            self.copy_bubble.toast("Couldn't save the key to the Keychain")
            return
        self.cloud = None
        self.copy_bubble.toast("Checking the key…", seconds=8)
        threading.Thread(target=self._test_key, args=(key,), daemon=True).start()

    def _test_key(self, key):
        err = CloudEditor(key, self.cfg["cloud_model"], self._get_editor, meter=self.spend).ping()
        if err is None:
            self.cfg["ai_engine"] = "cloud"
            save_config(self.cfg)
            self.cloud = None
            self.pending_toast = "Claude connected ✓"
            print("[cloud] key works; the AI now runs on Claude")
        else:
            self.pending_toast = f"Key didn't work: {err}"
            print(f"[cloud] key test failed: {err}")
        self.cloud_dirty = True

    def set_openai(self, _=None):
        """Three short questions: the service's address, the model, and your key (if any)."""
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        presets = "\n".join(f"• {name}: {url}" for name, url in OpenAIEditor.PRESETS)
        r = rumps.Window(title="OpenAI-style service (1 of 3): address",
                         message=f"Paste your service's address. Common ones:\n{presets}",
                         default_text=self.cfg.get("openai_base_url") or OpenAIEditor.PRESETS[0][1],
                         ok="Next", cancel="Cancel", dimensions=(360, 24)).run()
        url = (r.text or "").strip()
        if not r.clicked or not url:
            return
        r = rumps.Window(title="OpenAI-style service (2 of 3): model",
                         message=("Type the model name exactly as your service lists it "
                                  "(each service shows its model names on its website or app). "
                                  "A small, fast model is best: this is for quick text clean-up."),
                         default_text=self.cfg.get("openai_model") or "",
                         ok="Next", cancel="Cancel", dimensions=(360, 24)).run()
        model = (r.text or "").strip()
        if not r.clicked or not model:
            return
        r = rumps.Window(title="OpenAI-style service (3 of 3): API key",
                         message=("Paste your API key. Foni keeps it in your Mac's Keychain. "
                                  "Leave it empty for Ollama or LM Studio on this Mac. Only the "
                                  "text of dictations that need the AI is sent, never your audio."),
                         default_text="", ok="Save", cancel="Cancel", dimensions=(360, 24),
                         secure=True).run()
        if not r.clicked:
            return
        key = (r.text or "").strip()
        if key and not keychain_set(key, OPENAI_KEY):
            self.copy_bubble.toast("Couldn't save the key to the Keychain")
            return
        self.cfg["openai_base_url"], self.cfg["openai_model"] = url, model
        save_config(self.cfg)
        self.cloud = None
        self.copy_bubble.toast("Checking the service…", seconds=8)
        threading.Thread(target=self._test_openai, daemon=True).start()

    def _test_openai(self):
        ed = OpenAIEditor(self.cfg["openai_base_url"], self.cfg["openai_model"],
                          keychain_get(OPENAI_KEY), self._get_editor)
        err = ed.ping()
        if err is None:
            self.cfg["ai_engine"] = "openai"
            save_config(self.cfg)
            self.cloud = None
            self.pending_toast = f"{self.cfg['openai_model']} connected ✓"
        else:
            self.pending_toast = f"Didn't work: {err}"
        print(f"[cloud] OpenAI-style test: {err or 'ok'}")
        self.cloud_dirty = True

    # ---- first launch, permissions, login item, about
    @staticmethod
    def _bundle():
        path = NSBundle.mainBundle().bundlePath()
        return path if path and path.endswith(".app") else None

    @staticmethod
    def _login_service():
        try:
            from ServiceManagement import SMAppService
            return SMAppService.mainAppService()
        except Exception:
            return None

    def _login_enabled(self):
        svc = self._login_service()
        return bool(svc is not None and self._bundle() and svc.status() == 1)  # 1 = enabled

    def _set_login(self, on):
        svc = self._login_service()
        if svc is None or not self._bundle():
            return
        ok, err = (svc.registerAndReturnError_(None) if on else svc.unregisterAndReturnError_(None))
        print(f"[login] open at login {'on' if on else 'off'}: {'ok' if ok else err}")
        self.login_item.state = self._login_enabled()

    def toggle_login(self, item):
        self._set_login(not self._login_enabled())

    # ---- updates
    def _check_updates(self, manual=False):
        try:
            info = updates.latest_release()
        except Exception as e:
            print(f"[update] couldn't check: {type(e).__name__}")
            if manual:
                self.pending_toast = "Couldn't check for updates (internet?)"
            return
        if info and updates.is_newer(info[0], __version__):
            print(f"[update] Foni {info[0]} is available")
            self.update = info
            self.update_dialog_pending = manual
        elif manual:
            self.pending_toast = f"Foni {__version__} is the latest version ✓"

    def check_updates_now(self, _=None):
        self.update_checked = time.time()
        threading.Thread(target=self._check_updates, kwargs={"manual": True}, daemon=True).start()

    def show_update(self, _=None):
        if not self.update:
            return
        version, notes, dmg, page = self.update
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        short = notes.split("\n\n")[0].replace("**", "")[:400]
        answer = rumps.alert(title=f"Foni {version} is available",
                             message=f"You have {__version__}.\n\n{short}",
                             ok="Install and restart", cancel="Later", other="What's new")
        if answer == 1:
            if self._bundle():
                threading.Thread(target=self._install_update, daemon=True).start()
            else:
                subprocess.run(["open", page])
        elif answer == -1:
            subprocess.run(["open", page])

    def _install_update(self):
        version, _, dmg, page = self.update
        self.pending_toast = f"Downloading Foni {version}…"
        err = updates.install(dmg, self._bundle(), on_progress=self._progress("update"))
        self.setup_status = None
        if err:
            print(f"[update] not installed: {err}")
            self.pending_toast = "The update didn't install; opening the download page"
            subprocess.run(["open", page])
            return
        print(f"[update] installed Foni {version}; restarting")
        self.restart_pending = True  # restart from the main thread

    # ---- setup window
    def hotkey_name(self):
        return pretty(self.hotkeys)

    def show_setup(self, _=None):
        if self.setup is None:
            self.setup = SetupWindow(self)
        self.setup.show()

    def reset_permissions(self):
        """For when a switch in System Settings is on but macOS still says no (it remembers an
        older copy of Foni): clear Foni's permissions, then start again so you can allow them."""
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if rumps.alert(title="Reset Foni's permissions?",
                       message=("This turns off Foni's Microphone, Input Monitoring and "
                                "Accessibility permissions and restarts Foni, so you can turn them "
                                "on again. It fixes a switch that's on while Foni still shows ✗."),
                       ok="Reset and restart", cancel="Cancel") == 1:
            bundle_id = NSBundle.mainBundle().bundleIdentifier() or "com.jayandreadis.foni"
            subprocess.run(["tccutil", "reset", "All", bundle_id])
            print("[perm] permissions reset; restarting")
            self.restart()

    def _recheck_permissions(self):
        """After you allow Foni in System Settings, start listening without a restart."""
        self.perm_checked = time.time()
        listen, post = CGPreflightListenEventAccess(), CGPreflightPostEventAccess()
        if listen and getattr(self, "tap", None) is None:
            self._install_tap()
        if listen and post:
            self.perm_ok = True
            self.perm_item._menuitem.setHidden_(True)
            print("[perm] all permissions granted")

    def about(self, _):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if rumps.alert(title=f"Foni {__version__}",
                       message=("Hold a key, talk, and clean text appears where you type. Speech "
                                "to text runs on your Mac. Free and open source (MIT)."),
                       ok="Open on GitHub", cancel="Close") == 1:
            subprocess.run(["open", self.REPO])

    def _progress(self, what):
        def report(done, total):
            self.setup_status = (f"↓ {what} {100 * done // total}%" if total
                                 else f"↓ {what} {done / 1e9:.1f} GB")
        return report

    def _get_pill(self, key):
        key = key if key in PILL_STYLES else "fire"
        if key not in self.pills:
            self.pills[key] = PILL_STYLES[key]()
        return self.pills[key]

    def _style_icon(self):
        name = {st[0]: st[2] for st in self.STYLES if st}.get(self.cfg["pill_style"], "flame")
        icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, "Foni")
        if icon is not None:
            icon.setTemplate_(True)
        return icon

    def set_style(self, item):
        key = item.lf_key
        for st in self.STYLES:
            if st:
                self.style_menu[st[1]].state = st[0] == key
        if key == self.cfg["pill_style"]:
            return
        self.cfg["pill_style"] = key
        save_config(self.cfg)
        self.pill.hide()
        self.pill = self._get_pill(key)
        self.copy_bubble.set_accent(STYLE_ACCENTS.get(key))
        icon = self._style_icon()
        if icon is not None:
            self._icon_nsimage = icon
            self._nsapp.nsstatusitem.setImage_(icon)
        self.start_preview()  # show off the new look right away

    def toggle_idle_pill(self, item):
        item.state = not item.state
        self.cfg["show_idle_pill"] = bool(item.state)
        save_config(self.cfg)

    def copy_last(self, _):
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(self.last_text, NSPasteboardTypeString)

    def _dictionary_label(self):
        return f"Dictionary ({len(self.dictionary.entries)} learned)…"

    def _dictionary_item(self):
        self.dict_item = rumps.MenuItem(self._dictionary_label(), callback=self.open_dictionary)
        return self.dict_item

    def open_dictionary(self, _):
        if not LEARNED_PATH.exists():
            LEARNED_PATH.write_text("{}")
        subprocess.run(["open", "-t", str(LEARNED_PATH)])

    def _watch_edits(self, pasted, token):
        """Watch the text box you dictated into; learn from words you fix there."""
        try:
            time.sleep(0.6)
            el = focused_element()
            before = element_text(el) if el is not None else None
            if not before or pasted.strip() not in before:
                return  # can't read this box (some apps don't allow it)
            pasted_words = set(_words(pasted))
            latest, until = before, time.time() + 75
            while time.time() < until and self.watch_token == token:
                time.sleep(1.5)
                now = element_text(el)
                if now is None:
                    break
                # ignore states where the text is gone (e.g. message sent, box cleared)
                if now != latest and len(pasted_words & set(_words(now))) >= 0.5 * len(pasted_words):
                    latest = now
                f = focused_element()
                if f is None or not CFEqual(f, el):
                    break  # you moved on to another box or app
            for heard, meant in find_corrections(before, latest, pasted):
                times = self.dictionary.add(heard, meant)
                print(f"[dictionary] learned '{heard}' → '{meant}' (corrected {times}x)")
                self.pending_toast = f"Added “{meant}” to dictionary"
        except Exception as e:
            print(f"[dictionary] watcher stopped: {e}")

    def open_config(self, _):
        subprocess.run(["open", "-t", str(CONFIG_PATH)])

    def _reload_config_if_changed(self):
        """Pick up snippet / vocabulary / replacement edits without a restart."""
        try:
            mtime = CONFIG_PATH.stat().st_mtime
            if mtime != self.cfg_mtime:
                self.cfg_mtime = mtime
                fresh = json.loads(CONFIG_PATH.read_text())
                for k in ("vocabulary", "replacements", "snippets", "remove_fillers", "sounds",
                          "ai_editing", "show_pill", "show_idle_pill", "tail_seconds",
                          "min_hold_seconds", "double_tap_window", "app_styles"):
                    if k in fresh:
                        self.cfg[k] = fresh[k]
                self.writing_dirty = True
                print("[config] reloaded")
        except Exception as e:
            print(f"[config] not reloaded: {e}")

    # ---- hotkey (main thread, must be fast)
    @staticmethod
    def _check_permissions():
        """Keyboard access (to notice the hotkey) and typing access (to paste). Missing ones
        are asked for from the setup window, one button each, not with pop-ups at launch."""
        listen, post = CGPreflightListenEventAccess(), CGPreflightPostEventAccess()
        print(f"[perm] keyboard monitoring: {'ok' if listen else 'MISSING'} · "
              f"typing: {'ok' if post else 'MISSING'}")
        return bool(listen and post)

    def restart(self, _=None):
        app = self._bundle()
        if app:  # start a fresh copy once this one has quit
            subprocess.Popen(["/bin/sh", "-c", f"sleep 1; open -n '{app}'"], start_new_session=True)
        else:
            print("[restart] running from source; start it again yourself")
        rumps.quit_application()

    def _install_tap(self):
        mask = CGEventMaskBit(kCGEventFlagsChanged) | CGEventMaskBit(kCGEventKeyDown)
        self.tap = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                                    kCGEventTapOptionListenOnly, mask, self._on_event, None)
        if self.tap is None:
            print("[perm] can't watch the hotkey yet: turn Foni on under Input Monitoring "
                  "(Foni starts listening as soon as you do)")
            return
        src = CFMachPortCreateRunLoopSource(None, self.tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), src, kCFRunLoopCommonModes)
        CGEventTapEnable(self.tap, True)

    def _is_down(self, name, flags):
        _, side_flag, generic_flag = HOTKEYS[name]
        if not flags & generic_flag:
            return False
        if flags & side_flag:
            return True
        # keyboard doesn't say left/right: treat each event on this key as a toggle
        return name not in self.keys_down

    def _missed_release(self, flags, reason, skip=None):
        """macOS sometimes never reports a key being let go, e.g. when Ctrl is part of the
        Cmd+Ctrl+Shift+4 screenshot shortcut. Foni would then keep listening forever and
        ignore Fn. Drop any hotkey that `flags` (the keys really held right now) says is up.
        `skip`: the key this event is itself about (its own press/release is handled normally)."""
        gone = {n for n in self.keys_down if n != skip and not flags & HOTKEYS[n][2]}
        if not gone:
            return
        self.keys_down -= gone
        print(f"[hotkey] {pretty(sorted(gone))} was let go without macOS saying so ({reason})")
        if not self.keys_down:
            # what was recorded meanwhile wasn't meant as dictation: throw it away, don't paste it
            self.events.put(("chord", time.time()))

    def _check_stuck_keys(self):
        """Every tick: is the hotkey still physically held? (Fn isn't checked here: macOS
        doesn't reliably report it outside key events; any key press still catches it.)"""
        held = [n for n in self.keys_down if n != "fn"]
        if held:
            flags = CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState)
            if any(not flags & HOTKEYS[n][2] for n in held):
                self._missed_release(flags | (FN if "fn" in self.keys_down else 0), "keyboard check")

    def _on_event(self, proxy, etype, event, refcon):
        if etype in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
            CGEventTapEnable(self.tap, True)
            return event
        if self.keys_down:  # every key event carries the keys held right now
            own = (None if etype == kCGEventKeyDown else
                   KEYCODE_TO_NAME.get(CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)))
            self._missed_release(CGEventGetFlags(event), "next key event", skip=own)
        if etype == kCGEventKeyDown:
            if self.keys_down and not self.learning:
                self.events.put(("chord", time.time()))  # Option+key = special char, not dictation
            return event
        name = KEYCODE_TO_NAME.get(CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode))
        if name is None:
            return event
        flags = CGEventGetFlags(event)
        down = self._is_down(name, flags)
        if self.learning:
            if down:
                self.learned, self.learning = name, False
            return event
        if name not in self.hotkeys:
            # command modifier pressed while the hotkey is held -> voice command
            if down and self.keys_down and HOTKEYS[name][2] == self.command_mod:
                self.events.put(("command", time.time()))
            return event
        was = bool(self.keys_down)
        (self.keys_down.add if down else self.keys_down.discard)(name)
        if was != bool(self.keys_down):
            now = time.time()
            self.events.put(("press" if self.keys_down else "release", now))
            if self.keys_down and flags & self.command_mod:
                self.events.put(("command", now))  # modifier was already held
        return event

    # ---- state machine (own thread)
    def _controller(self):
        press_t = 0.0
        last_tap = 0.0
        swallow_release = False
        while True:
            kind, t = self.events.get()
            if self.state == "loading":
                continue
            if kind == "press":
                if self.state == "latched":          # tap to finish hands-free mode
                    swallow_release = True
                    self._finish()
                    continue
                press_t = t
                swallow_release = False  # a fresh press: any release we were waiting for is past
                if self.state != "recording":
                    try:
                        self.recorder.start()
                    except Exception as e:
                        print(f"[mic] {e}")
                        continue
                    self.command = False
                    self.app = frontmost_app()
                    self.rec_started = time.time()
                    self.state = "recording"
                    self.press_id += 1
                    threading.Timer(0.18, self._start_cue, args=(self.press_id,)).start()
            elif kind == "command":
                if self.state == "recording" and not self.command:
                    self.command = True
                    if self.cfg.get("ai_engine") != "cloud":
                        self._ensure_editor()  # voice commands always need the AI
                    play("Pop", self.cfg)
            elif kind == "release":
                if swallow_release:
                    swallow_release = False
                    continue
                if self.state != "recording":
                    continue
                if t - press_t < self.cfg["min_hold_seconds"] and not self.command:   # a tap
                    if t - last_tap < self.cfg["double_tap_window"] + self.cfg["min_hold_seconds"]:
                        self.state = "latched"                    # double tap -> hands-free
                        last_tap = 0.0
                        play("Pop", self.cfg)
                    else:
                        last_tap = t
                        self.recorder.stop()
                        self.state = "idle"
                else:
                    self._finish()
            elif kind == "chord" and self.state == "recording":
                self.recorder.stop()
                self.state = "idle"
                swallow_release = False

    def _start_cue(self, press_id):
        if self.state == "recording" and self.press_id == press_id:
            play("Tink", self.cfg)

    def _finish(self):
        time.sleep(self.cfg["tail_seconds"])  # people let go while finishing the last word
        audio = self.recorder.stop()
        self.state = "working"
        self.jobs.put((audio, self.command, self.app))

    # ---- AI editor memory: loaded on demand, freed when idle
    def _ensure_editor(self):
        """Start loading the AI editor in the background if it isn't loaded (non-blocking)."""
        self.ai_last_used = time.time()
        with self.editor_lock:
            if self.editor is not None or self.editor_loading:
                return
            self.editor_loading = True
            self.editor_ready.clear()
        threading.Thread(target=self._load_editor, daemon=True).start()

    def _load_editor(self, warm_only=False):
        try:
            if not is_downloaded(self.cfg["ai_model"]):
                self.pending_toast = "Downloading the Mac AI model (~2 GB). Dictation works meanwhile."
            self.editor = AIEditor(self.cfg["ai_model"], self.cfg.get("ai_draft_model"),
                                   warm=not self.editor_warmed, on_progress=self._progress("AI"))
            self.editor_warmed = True
        except Exception as e:
            print(f"[model] AI editor unavailable ({e}); dictation still works without it")
        finally:
            self.setup_status = None
            # warm-up at launch: count it as long unused, so it's freed right away
            self.ai_last_used = 0.0 if warm_only else time.time()
            self.editor_loading = False
            self.editor_ready.set()

    def _get_editor(self):
        """The editor, for a dictation that needs it; waits for a load if one is underway."""
        if self.editor is None:
            t = time.time()
            self._ensure_editor()
            self.editor_ready.wait(timeout=90)
            print(f"[memory] waited {time.time() - t:.1f}s for the AI editor to load")
        self.ai_last_used = time.time()
        return self.editor

    def _free_editor(self):
        import gc
        import mlx.core as mx
        self.editor_ready.wait(timeout=90)  # never free a model that's halfway loaded
        self.editor = None
        gc.collect()
        mx.clear_cache()

    def _free_editor_if_idle(self):
        minutes = self.cfg.get("ai_unload_after_minutes") or 0
        if self.ai_last_used == 0.0:
            minutes = minutes or 1e-9  # launch warm-up: free it even if freeing is turned off
        if (minutes and self.editor is not None and not self.editor_loading
                and self.state == "idle" and time.time() - self.ai_last_used > minutes * 60):
            self._free_editor()
            print("[memory] AI editor freed (~4 GB); it reloads when a dictation needs it")

    # ---- models + transcription (one thread: MLX streams are per-thread)
    def _worker(self):
        import mlx.core as mx
        mx.set_cache_limit(256 * 1024 * 1024)  # don't hoard freed GPU buffers between dictations
        try:
            self.transcriber = Transcriber(self.cfg, on_progress=self._progress("speech model"))
        except Exception as e:
            print(f"[model] speech model failed to load: {e}")
            self.setup_status = "⚠︎ no speech model (internet?)"
            return
        self.setup_status = None
        # The model file is full precision; loading halves it. Hand the full-size copy, now
        # spare, back to macOS right away (~0.5 GB) instead of keeping it in MLX's cache.
        mx.clear_cache()
        self.state = "idle"
        play("Glass", self.cfg)
        print(f"[ready] {self._hint()} · {self._command_hint()}")
        # Mac AI: load it once in the background so later loads are quick (~3 s), then free it
        # until a dictation needs it. Not if it isn't downloaded yet (that waits until a
        # dictation needs it), nor when a cloud service does the AI.
        if self.cfg.get("ai_engine") == "local" and is_downloaded(self.cfg["ai_model"]):
            with self.editor_lock:
                self.editor_loading = True
                self.editor_ready.clear()
            threading.Thread(target=self._load_editor, kwargs={"warm_only": True},
                             daemon=True).start()
        while True:
            try:
                job = self.jobs.get(timeout=15)
            except queue.Empty:
                self._free_editor_if_idle()
                continue
            if job is None:  # model switch requested from the menu
                self._free_editor()
                self.editor_warmed = False
                if self.cfg.get("ai_engine") == "local":  # in the cloud it loads only if needed
                    self._ensure_editor()
                continue
            audio, command, app = job
            try:
                self._reload_config_if_changed()
                self._handle(audio, command, app)
            except Exception as e:
                print(f"[error] {e}")
            if self.state == "working":
                self.state = "idle"

    def _handle(self, audio, command, app):
        dur = len(audio) / SAMPLE_RATE
        if dur < 0.3 or not has_speech(audio):
            print("[skip] too short or silent")
            return
        saved = selected = None
        if command:
            wait_for_modifiers_released()
            selected, saved = grab_selection()
        t0 = time.time()
        raw = self.transcriber.transcribe(audio)
        t1 = time.time()
        if command:
            editor = self._get_ai()
            if not editor:
                print("[command] AI editor not available")
                if saved:
                    restore_clipboard(saved)
                return
            text = editor.command(raw.replace("<unk>", ""), selected)
            tag = "command on selection" if selected else "command"
        else:
            text, used_ai = postprocess(raw, self.cfg, self._get_ai, app, self.dictionary)
            tag = "ai" if used_ai else "plain"
            group = style_group(app)
            style = (self.cfg.get("app_styles") or {}).get(group, "formal") if group else None
            if style and style != "formal":
                keep = set(self.cfg.get("vocabulary") or [])
                keep |= {w for e in self.dictionary.entries.values() for w in str(e.get("to", "")).split()}
                text = apply_style(text, style, keep)
                tag += f" · {WRITING_STYLES[style][0].lower()}"
        t2 = time.time()
        print(f"[{dur:.1f}s audio · {app or '?'} · {tag}] asr {t1 - t0:.2f}s, "
              f"edit {t2 - t1:.2f}s → {text!r}")
        if not text:
            return
        self.last_text = text
        if command and selected:
            target, why = True, "replacing selection"
        else:
            target, why = focused_text_target()
        if target is False:
            print(f"[paste] no text box ({why}) → showing Copy button")
            self.pending_copy = text
            if saved:
                restore_clipboard(saved)
        else:
            print(f"[paste] {'text box' if target else 'not sure, pasting + Copy button'}: {why}")
            wait_for_modifiers_released()
            type_text(text if command else text + " ", saved)
            if target is None:  # can't tell if it landed: offer Copy as a fallback
                self.pending_copy = text
            if not command:
                self.watch_token += 1
                threading.Thread(target=self._watch_edits, args=(text, self.watch_token),
                                 daemon=True).start()
        rec = {"t": time.time(), "audio_s": round(dur, 2), "app": app, "mode": tag,
               "pasted": target is not False, "raw": raw, "text": text}
        if command:
            rec["selected"] = selected
        with HISTORY_PATH.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.stats.add(rec)
        self.stats_dirty = True
