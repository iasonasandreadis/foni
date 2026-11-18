# Foni

Hold a key, talk, let go: clean text appears wherever you're typing. A free, open-source alternative to Wispr Flow for Apple Silicon Macs.

- **Speech to text runs on your Mac** (NVIDIA Parakeet, on the Apple GPU). Your voice never leaves it.
- **AI clean-up with your own account.** Plug in your own Claude API key, or any OpenAI-style service. Or keep that on your Mac too, free and offline.
- **Free.** No subscription and no word limits. With a Claude key, the AI clean-up costs fractions of a cent: about 2 cents a month at 7 clean-ups a day.

Website: [jayandreadis.com/foni](https://jayandreadis.com/foni) · Download: [latest release](https://github.com/iasonasandreadis/foni/releases/latest)

## What it does

| Do this | Get this |
|---|---|
| Hold **Fn**, talk, let go | Your words, cleaned up, typed where your cursor is |
| Double-tap **Fn** | Hands-free; tap again to finish |
| Hold **Fn + Control**, talk | Ask the AI: rewrite the selected text ("make this more formal", "translate this to Greek"), or write something new ("draft a reply saying I can't make it Friday") |

- **Clean-up that stays faithful.** It removes "um"s and stutters and keeps only your correction ("Tuesday, no wait, Wednesday" → "Wednesday"). It formats spoken lists and keeps your question marks. A safety check throws away any AI edit that rewords you, answers you, or drops a question.
- **Only when needed.** By default the AI only touches messy dictation, so clean sentences are instant.
- **Writing style per app.** Casual in WhatsApp and in AI apps like Claude, formal in email, unchanged in code editors. Works in Greek too.
- **Learns your words.** Fix a misheard word right after dictating (for example "rabbits" → "RAM") and Foni remembers it.
- **Always types.** When macOS can't confirm there's a text box, Foni types anyway and also shows a Copy button, so nothing you said is lost.
- **Mutes your Mac while you talk.** A FaceTime call, a video or music playing doesn't get typed too. Your sound comes back the moment you let go.
- **Seven looks.** A small pill at the bottom of the screen shows that Foni is listening: Fire, Bubbles, Stardust, Hearts, Minimal, Rainbow or Retro equalizer.
- **25 languages**, including English, Greek, Spanish, French and German.

## Install

1. Download [Foni.dmg](https://github.com/iasonasandreadis/foni/releases/latest/download/Foni.dmg) (239 MB), open it, and drag **Foni** to **Applications**.
2. Open Foni. macOS says it can't check the app, because Foni isn't signed with a paid Apple developer account. Go to **System Settings → Privacy & Security**, scroll down, and click **Open Anyway** next to Foni.
3. Foni opens a setup window with a live checklist. Allow the three permissions: **Microphone** lets it hear you, **Input Monitoring** lets it notice the key, and **Accessibility** lets it type for you (on macOS 27 this one is called **Device Control and Data Access**). If a switch is on but the checklist still shows ✗, click **Reset permissions** in that window.
4. The first time, Foni downloads its speech model (about 2.5 GB). The menu bar shows the progress.

Tip: in **System Settings → Keyboard**, set "Press 🌐 key to" to "Do Nothing", so Fn doesn't also open the emoji picker.

Needs an Apple Silicon Mac (M1 or newer) with macOS 14 or later.

## The AI: choose where it runs

Menu bar → **AI runs on**:

- **This Mac.** A small Qwen model, private and offline. It downloads about 2 GB the first time it's needed and uses that much memory only while it's working.
- **Claude.** Paste your own [Claude API key](https://console.anthropic.com). Foni uses Claude Haiku, which is fast and great at Greek. The menu shows exactly what it has cost you. A Claude.ai Pro or Max subscription can't be used by other apps; you need an API key, which is pay-as-you-go.
- **OpenAI-style service.** OpenAI, OpenRouter or Groq with your key, or a local Ollama or LM Studio server without one.

Only the text of a dictation that needs the AI is sent, never your audio. Keys are stored in your Mac's Keychain.

## Updates

Foni checks GitHub for a newer version 10 seconds after it starts, then every 6 hours. When there is one, the menu shows **Update to Foni x.y.z**: one click downloads it, checks it's signed by the same developer, swaps it in and restarts (about 15 seconds), and your permissions stay on. While it works, the menu shows how far it is. When you're up to date, the menu says so (**✓ Foni x.y.z is up to date**); click it to check again. Turn the check off with `"check_updates": false` in the settings file.

Coming from 1.0.0–1.0.2 (before the updater existed): download Foni.dmg once more and replace the app. On 1.0.3–1.0.5 the menu item is called **Check for updates…**, then **Install and restart**.

## Memory and speed

Measured on a MacBook Air M4 (16 GB), with the AI on Claude:

- About **1.6 GB** of memory while you use it, almost all of it the speech model, which stays loaded so dictation is instant.
- About **0.5 s** from letting go of the key to the text appearing, for a typical sentence.

## Settings

Most things are in the menu. The rest (vocabulary, snippets like "my email" → your address, word replacements) are in `~/.foni/config.json`: menu → **Edit snippets & settings…**. Your dictionary, history and log are also in `~/.foni`.

## Uninstall

Quit Foni from its menu, then delete **Foni** from Applications, the `~/.foni` folder, and the speech model in `~/.cache/huggingface/hub` (the folder named `models--mlx-community--parakeet-tdt-0.6b-v3`).

## Build it yourself

```bash
git clone https://github.com/iasonasandreadis/foni && cd foni
./scripts/build.sh
```

This makes `build/Foni.app` and the `.dmg`, using a self-contained Python. It needs Xcode Command Line Tools. To run from source instead:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m foni
```

Tests: `.venv/bin/python -m pytest`

## Credits

Built by [Jay Andreadis](https://jayandreadis.com). Speech recognition: [NVIDIA Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) (CC BY 4.0), run with [parakeet-mlx](https://github.com/senstella/parakeet-mlx) and [MLX](https://github.com/ml-explore/mlx). Mac AI: [Qwen2.5](https://huggingface.co/Qwen) via [mlx-lm](https://github.com/ml-explore/mlx-lm). Foni downloads these models from Hugging Face; each has its own license.

## License

MIT. See [LICENSE](LICENSE).
