# Niko — process summary

## 1. Honest status in one paragraph

`niko.py` (version 2) is a full rewrite of your original file that keeps its database, its settings file, its command list and its buttons, and replaces Ollama with the GPT4All local API. **69 automated tests pass**, but they ran on a Linux computer using fake stand-ins for Tkinter, the GPT4All server, the microphone and macOS commands. **Nothing has been run on your MacBook Air.** Section 8 separates what was tested from what still needs your Mac.

## 2. What the original contained

I read your uploaded `niko.py`, `README.md`, `PROFILE.txt` and `Start_Niko.command` before changing anything.

- **Window:** Tkinter, 390×570, dark theme, header orb, chat box, input + SEND, buttons: Speech ON/OFF, Memory, Setup help.
- **Database:** `~/Library/Application Support/Niko/niko_memory.sqlite3` with two tables:
  - `memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL)`
  - `chat (id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL)`
- **Settings:** `settings.json` with `ollama_model`, `ollama_url`, `speak_replies`, `window_alpha`.
- **Commands:** `remember that …`, `what do you remember about me`, `clear chat`, `open/launch/start <app or site>`, `search for …` / `google …`, `email a | b | c`.
- **Allow-lists:** 17 app names/aliases (`SAFE_APPS`) and 7 websites (`SITES`).
- **AI:** Ollama over HTTP. **Speech:** macOS `say`.

## 3. What I kept exactly

- Table names and column definitions: `memories` and `chat`. The same `CREATE TABLE IF NOT EXISTS` statements are used, so existing tables are never touched. **No `facts` or `messages` tables exist anywhere.**
- Every entry of `SAFE_APPS` and `SITES`, and every original command phrase.
- `clear chat` deletes only from `chat`.
- Settings keys, including the old `ollama_*` keys. They stay in your file but are never used.
- The Memory button, Speech ON/OFF button, Setup help button, colours and layout style.
- Email: confirmation first, creates a draft only. The script contains no AppleScript "send" command.

## 4. Bugs found in the original and repaired

1. **A dialog was opened from a background thread.** The email confirmation (`messagebox.askyesno`) ran inside the worker thread. On macOS this can crash or hang Tk. Now the worker hands the request to the main thread, which shows the dialog, and the approved action then runs in a new background thread.
2. **Your message was sent to the AI twice.** It was saved to the chat table first, then loaded back as "history", then appended again. Now the history is read without the new message, and the exchange is saved afterwards.
3. **The WhatsApp → WhatsApp Web fallback never triggered**, because `subprocess.Popen` doesn't report failure. Now the exit code is checked, and failures to open an app are reported honestly ("Is it installed?") instead of "Opening X."
4. **Overlapping speech.** Each reply started a new `say` process on top of the last. Now a new reply (or pressing Mic, or turning Speech OFF, or closing the window) stops the previous speech.
5. **Error messages were saved into chat history** and fed back to the model. Errors are now shown but not saved or spoken.
6. **Text starting with "-" or containing odd characters** could be treated as an option by `say`. Text is now sent through standard input.
7. **"start a story about …"** was refused as an unknown app. Now `start` with an unknown name is treated as normal chat. (`open` and `launch` still refuse unknown apps.)
8. **Possible crash with emoji** in replies on older Tk versions. Characters Tk can't draw are removed on those versions.
9. **A hand-edited or corrupt `settings.json`** silently fell back to defaults and overwrote the file. Now a copy is kept first and wrong value types are repaired.

## 5. What I added

- **GPT4All connection** (replaces Ollama): base URL `http://127.0.0.1:4891/v1`; reads `/models` to find the model name (prefers a name containing "phi", or your `gpt4all_model` setting); sends chats to `/chat/completions`. Handles server off, no models, timeouts, wrong model name (re-discovers once), HTTP errors, unreadable replies. Local requests bypass any system proxy. The window shows "AI: ready / offline", and `check ai` tests it.
- **Smaller prompts for an 8 GB / old CPU setup:** last 6 chat messages, 20 memories, answers capped at 400 tokens, 240-second timeout (all adjustable). History is cleaned so roles strictly alternate user/assistant, which chat templates need.
- **Microphone:** Mic button; press to start, press again to stop (30-second limit); never records in the background; text goes into the input box. Works only if the optional packages are installed. A one-time notice says audio is sent to Google.
- **Optional online AI:** a provider class for any OpenAI-style API, switched off. It needs `online_enabled`, a model name and your own key, plus a typed `ask online:` command and a Yes click. It sends only that question (no memories or history).
- **Safety extras:** automatic database backups on every start (newest 10 kept) in `Backups/`; a copy of your old settings before the first migration; the app refuses to run (without changing anything) if your database tables have unexpected columns.
- **Interface:** scrollbar, larger text, Memory window listing all memories with dates, `help` and `check ai` commands, window closes cleanly.

## 6. Files in the bundle

| File | Purpose |
|---|---|
| `niko.py` | the application |
| `Start_Niko.command` | double-click launcher (checks Python and Tkinter first) |
| `NIKO_README.md` | beginner setup guide |
| `NIKO_PROCESS_SUMMARY.md` | this file |
| `requirements-microphone.txt` | optional microphone packages |
| `test_niko.py` | the self-tests (safe to run; uses a temporary folder) |

Your original `niko.py`, database and settings are not modified by anything I produced.

## 7. How things work

**Memory.** `remember that X` inserts a row into `memories`. The model sees up to 20 of the newest memories in its instructions. The Memory button and `what do you remember about me` read the same table. Chat messages go into `chat`; `clear chat` empties only that table.

**GPT4All.** Niko calls `GET /models`, picks the model, then `POST /chat/completions` (non-streaming). No model name is hard-coded. Nothing contacts Ollama.

**Voice out.** `say` runs as a separate process, so the window never freezes. The optional `voice` setting picks a voice.

**Voice in.** Records from the Mac microphone with PyAudio after you press Mic, then uses `recognize_google` from SpeechRecognition. **This sends the audio to Google; it is not offline.**

**Threads.** Slow work (AI, opening things, recording) runs in background threads. They never touch the window or open dialogs. They put results in a queue that the main thread reads using `root.after`.

## 8. Tests

### Actually performed (69 automated tests, all passing)
Environment: Linux, Python 3.12, standard library only, fake Tkinter, fake speech module, mock HTTP servers, mocked `open` / `osascript` / `say`.

| # | Your requirement | What was done |
|---|---|---|
| 1 | Inspect original source | Read fully; bugs in section 4 found and confirmed |
| 2 | Inspect SQLite schema | **From the original code only.** I did not have your real database file. |
| 3–4 | Old memory/chat records stay compatible | Built a database using the original table definitions plus sample rows (including accents, Chinese characters, multi-line text); opened it with the new code; rows, timestamps and table definitions were identical afterwards; no new tables |
| 5 | `clear chat` keeps memories | Tested directly, via the command, and via the (fake) window |
| 6 | Python syntax | Compiles (3.12 only; not tried on 3.8/3.9/3.11) |
| 7 | Imports | Only standard-library imports; `speech_recognition` is imported lazily; real `tkinter` was **not** available here |
| 8–9 | GPT4All connection and model discovery | Against a mock OpenAI-style server: discovery, preference for "phi", configured model found/missing, retry after 404, empty model list, non-JSON reply |
| – | Error handling | Server offline, timeout, HTTP 500, empty and malformed replies |
| 10 | Voice output | `say` command line built correctly, text via stdin, previous speech interrupted, bad voice name ignored, missing `say` doesn't crash (all mocked) |
| 11 | Microphone handling | With a fake speech module: success, not installed, PyAudio missing, no device, not understood, offline |
| 12–14 | App launching, websites, Google search | Every original alias and site checked; unknown apps refused; WhatsApp fallback; URL encoding. Commands were *recorded*, not executed |
| 15 | Email confirmation | Nothing runs before confirmation; declining creates nothing; accepting runs one `osascript`; quotes/injection escaped; no send command exists. AppleScript was *not* executed |
| 16 | GUI thread safety | Static check that worker functions don't touch widgets or dialogs; the dialog was confirmed to run on the main thread; double-send blocked while busy |
| 17 | Settings migration | Old-style settings keep every key and value; backup created; second run changes nothing; corrupt file saved aside; wrong types repaired. Tested with a synthetic old file, **not your real one** |
| 18 | No Ollama | No Ollama address or code path in the file (checked by test) |

### Needs your Mac (not tested)
- The real Tkinter window (looks, buttons, colours, scrolling, focus, closing).
- The real GPT4All server and your actual Phi-3 model name and response speed.
- Real `say` voice, real microphone, PyAudio installation, macOS permission pop-ups.
- Real `open -a`, Apple Mail drafts and Automation permission.
- Your real database and real `settings.json` (the checklist in `NIKO_README.md` section 9 covers this).
- Online mode against a real provider.

## 9. Known limitations

- GPT4All's API can behave differently between versions; if `check ai` fails, tell me the exact message.
- Phi-3 on a 2015 Air will be slow, especially for the first answer.
- Voice input needs internet and sends audio to Google. PyAudio may be hard to install on old macOS.
- Automatic switching to online AI for "difficult" questions is **not** implemented; you use `ask online:`.
- Memories can't be deleted from inside Niko (this was also true in the original). You can only add and view.
- Niko can't send email, and has no wake word or always-on listening, by design.
- The window uses ordinary Tkinter, so it won't look as polished as a native app.
