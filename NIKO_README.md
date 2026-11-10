# Niko — setup guide (version 2)

Niko is a small desktop assistant for your Mac. It chats using the **Phi-3 Mini** model you already downloaded in **GPT4All**, remembers things you tell it, opens apps and websites, searches Google, and prepares email drafts. No Ollama needed.

> **Important:** this version has been tested with automated tests on another computer, **not on your Mac**. Section 9 is a short checklist you can run to confirm everything on your own machine.

---

## 1. What you need

| Thing | Why | Notes |
|---|---|---|
| Python 3 with Tkinter | runs Niko | Install from https://www.python.org/downloads/macos/ (Python 3.11 is a good choice for Monterey). Apple's built-in Python often has a weak Tkinter and can show a blank window. |
| GPT4All desktop app | runs Phi-3 | You already have it, with Phi-3 Mini Instruct downloaded. |
| Internet | **only** for voice input and the optional online mode | Normal chat with Phi-3 works offline. |

Niko itself uses only Python's built-in tools. Nothing to install for the core app.

## 2. Put the files in a folder

Unzip `Niko_Complete_Bundle.zip`. Put the folder somewhere easy, such as your Desktop. Your old `niko.py` is not touched by this process. If you want a safety copy, copy it somewhere else first.

Your memories are **not** in that folder. They stay in:
`~/Library/Application Support/Niko/`
so replacing `niko.py` never deletes them.

## 3. Turn on the GPT4All Local API Server

1. Open the **GPT4All** app.
2. Click **Settings** (the gear) → **Application**.
3. Turn **ON** "Enable Local API Server". The port should be **4891**.
4. Keep GPT4All open while you use Niko.
5. Click once into a chat in GPT4All and make sure Phi-3 Mini is selected/loaded (needed on some versions).

Niko asks the server which models exist and picks one containing "phi" in its name. It does not guess a model name. If your model has a different name and Niko picks the wrong one, see "Choose a specific model" below.

## 4. Start Niko

**Option A (easiest):** double-click `Start_Niko.command`.
If macOS blocks it: right-click the file → **Open** → **Open**.
If it says "permission denied", open Terminal and run:

```
cd ~/Desktop/Niko_Complete_Bundle
chmod +x Start_Niko.command
```

**Option B (Terminal):**

```
cd ~/Desktop/Niko_Complete_Bundle
python3 niko.py
```

(Change the folder name if you put it somewhere else. Tip: type `cd ` then drag the folder into the Terminal window.)

In the top-right of the Niko window you'll see **AI: ready** or **AI: offline**. You can also type `check ai`.

## 5. Commands

| Say this | What happens |
|---|---|
| `open Chrome`, `open Safari`, `open WhatsApp`, `open VS Code`, `open Notes` … | Opens an approved app. Approved list: Chrome, Safari, Firefox, WhatsApp, Messages, Mail, Notes, Calendar, Finder, VS Code, Terminal, Spotify, Photos. |
| `open YouTube`, `open Gmail`, `open GitHub`, `open Instagram`, `open ChatGPT`, `open Google`, `open WhatsApp Web` | Opens the website. |
| `search for <words>` or `google <words>` | Opens a Google search. |
| `remember that <something>` | Saves it to memory. |
| `what do you remember about me` | Lists saved memories. (The **Memory** button shows them with dates.) |
| `clear chat` | Clears the conversation only. **Never** deletes memories. |
| `email name@example.com \| Subject \| Message` | Asks you to confirm, then creates a **draft** in Apple Mail. You press Send yourself. Niko cannot send email. |
| `check ai` | Tests the GPT4All connection. |
| `help` | Shows this list inside Niko. |
| `ask online: <question>` | Optional, see section 8. |
| anything else | Chats with your local Phi-3 model. |

Apps not on the list are refused on purpose. To add one, open `niko.py` in VS Code, find `SAFE_APPS`, and add a line like `"slack": "Slack",` (the right side must be the app's exact name in Applications).

Buttons: **Mic**, **Speech: ON/OFF**, **Memory**, **Setup help**.

**Email permission:** the first time, macOS may ask whether Terminal can control Mail. Click OK. If you missed it: System Preferences → Security & Privacy → Privacy → Automation → tick Mail under Terminal.

## 6. Microphone (optional)

> **Privacy:** Niko uses Google's online speech recognition (`recognize_google`). When you record, **the audio is sent to Google**. This is not offline. Niko shows a notice and asks before your first recording.

Niko only records when you press **Mic**. Press **Stop** to finish (it also stops after 30 seconds). It never listens in the background. The text appears in the input box so you can check it before pressing Send.

One-time setup in Terminal:

```
brew install portaudio
python3 -m pip install -r requirements-microphone.txt
```

- `brew` is Homebrew (https://brew.sh). If Homebrew doesn't work on your older Mac, voice input may not be installable. Niko works fine without it.
- On first use macOS asks if Terminal may use the microphone. Click **OK**. If you clicked Don't Allow: System Preferences → Security & Privacy → Privacy → Microphone → tick Terminal.
- To send straight away without checking, set `"mic_auto_send": true` in settings (section 7).

## 7. Settings

File: `~/Library/Application Support/Niko/settings.json`. Niko adds new keys the first time it runs and keeps your old ones (including the old Ollama ones, which are ignored). A copy of your old file is saved as `settings.json.bak-before-gpt4all`.

| Key | Meaning |
|---|---|
| `speak_replies` | true/false, same as the Speech button |
| `window_alpha` | window transparency, 0.3 – 1.0 |
| `voice` | a Mac voice name such as `"Samantha"` (blank = default). See voices with `say -v '?'` in Terminal. |
| `gpt4all_base_url` | default `http://127.0.0.1:4891/v1` |
| `gpt4all_model` | blank = auto-detect. Or put the exact model name shown by `check ai`. |
| `gpt4all_timeout_seconds` | how long to wait for an answer (default 240; Phi-3 can be slow on a 2015 Air) |
| `gpt4all_max_tokens` | maximum length of an answer (default 400) |
| `history_messages`, `memories_in_prompt` | how much context to send (smaller = faster) |

**Choose a specific model:** type `check ai` and copy the exact name after "Using model" or from "Models reported" into `"gpt4all_model"`.

Edit the file in VS Code, save, and restart Niko. Keep the commas and quotes exactly as they are.

## 8. Online mode (optional, off by default)

The code is in place, but it does nothing until **you** configure it. Niko does not include any API key and never makes paid calls on its own.

To turn it on you need an account with a provider that offers an OpenAI-style API and your own key. Then:

1. In Terminal, set your key (best option, so it isn't saved in a file): `export NIKO_ONLINE_API_KEY="your-key"` and start Niko from that same Terminal window with `python3 niko.py`.
2. In `settings.json`, set `"online_enabled": true`, set `"online_model"` to a model name your provider offers, and change `"online_base_url"` if you're not using the default.
3. Ask with `ask online: your question`. Niko shows a confirmation first. Only that question is sent; your memories and chat history are not.

**Not implemented:** automatic switching to online for "serious" questions. You choose, per question, with `ask online:`. I have not tested online mode against a real provider.

## 9. Check it on your Mac (5 minutes)

1. Start Niko. Does the window appear?
2. Type `check ai`. Do you see "Connected to GPT4All" and your model?
3. Ask: `Hi Niko, who are you?` (first answers can take a minute).
4. `remember that my favourite colour is blue` → click **Memory**. Is it listed along with your old memories?
5. `clear chat`, then click **Memory**. Are the memories still there?
6. `open Chrome`, `open YouTube`, `search for test`.
7. Press **Speech: OFF/ON** and make sure Niko speaks.
8. `email you@example.com | Test | Hello` → click **No** (nothing should happen), then repeat and click **Yes** (a draft appears in Mail; it's *not* sent).
9. Optional: **Mic** (after section 6).

You can also run the built-in self-tests (they use a temporary folder and never touch your real memory):

```
cd ~/Desktop/Niko_Complete_Bundle
python3 test_niko.py
```

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| "I can't reach GPT4All" / AI: offline | GPT4All isn't open, or Local API Server is off (section 3). |
| "reports no models" | In GPT4All, make sure Phi-3 is downloaded and select it once in a chat. |
| "didn't finish within … seconds" | The Mac is slow. Ask shorter questions, close other apps, or raise `gpt4all_timeout_seconds`. |
| "Your settings ask for the model …" | The `gpt4all_model` setting doesn't match. Set it to `""` or the exact name from `check ai`. |
| Window is blank or black | Install Python from python.org (section 1) and use that. |
| "This Python has no Tkinter" | Same fix. |
| Mic button says PyAudio isn't installed | Section 6 commands. |
| "I couldn't understand that" | Speak closer, check microphone permission for Terminal, check internet. |
| Email doesn't open | Allow Terminal to control Mail (section 5). Make sure Mail has an account set up. |
| "I don't have 'X' on my approved app list" | Add it to `SAFE_APPS` (section 5). |
| Niko says the database has a problem | Niko stopped without changing anything. Don't delete anything; restore from a backup (section 11). |

## 11. Back up your memory

Niko automatically copies the database into `~/Library/Application Support/Niko/Backups/` each time it starts (newest 10 are kept).

Manual backup: open Terminal and run

```
cp ~/Library/Application\ Support/Niko/niko_memory.sqlite3 ~/Desktop/niko_memory_backup.sqlite3
```

To restore: quit Niko, then copy the backup file over `niko_memory.sqlite3` in that folder.

Please don't save passwords or banking details as memories. Saved memories are included in what is sent to your local model (never to the internet, unless you use `ask online`, which does not include memories).
