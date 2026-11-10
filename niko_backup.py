#!/usr/bin/env python3
"""
NIKO - personal desktop assistant for macOS (version 2).

* Local AI: your Phi-3 Mini model, served by the GPT4All desktop app (no Ollama).
* Memory: the SAME SQLite file and the SAME tables as before ("memories", "chat").
* Safe Mac commands: allow-listed apps and websites, Google search.
* Apple Mail drafts: always asks first, never sends.
* Voice out: macOS "say".  Voice in: optional microphone button (press to record).
* Optional online AI: code is ready, switched off until you add your own API key.

The core of this file needs only the Python standard library.
The microphone feature is optional (see requirements-microphone.txt).
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

try:  # Tkinter is only needed for the window, not for the logic below.
    import tkinter as tk
    from tkinter import messagebox
except ImportError:  # pragma: no cover
    tk = None
    messagebox = None

__version__ = "2.0.0"

# --------------------------------------------------------------------------
# Paths (identical to the original Niko).  NIKO_HOME is only for testing.
# --------------------------------------------------------------------------
APP_DIR = Path(os.environ.get("NIKO_HOME") or (Path.home() / "Library" / "Application Support" / "Niko"))
DB_PATH = APP_DIR / "niko_memory.sqlite3"
SETTINGS_PATH = APP_DIR / "settings.json"
BACKUP_DIR = APP_DIR / "Backups"

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
# NOTE: the old "ollama_model" / "ollama_url" keys are NOT listed here. If they
# exist in your settings.json they are kept untouched (just never used).
DEFAULT_SETTINGS: Dict[str, object] = {
    "speak_replies": True,
    "window_alpha": 0.96,
    "always_on_top": True,
    "voice": "",                       # e.g. "Samantha". Empty = macOS default voice.
    # --- local AI (GPT4All) ---
    "ai_backend": "gpt4all",
    "gpt4all_base_url": "http://127.0.0.1:4891/v1",
    "gpt4all_model": "",               # empty = auto-detect from /models (prefers "phi")
    "gpt4all_timeout_seconds": 240,    # a 2015 MacBook Air can be slow
    "gpt4all_max_tokens": 400,
    "gpt4all_temperature": 0.65,
    "history_messages": 6,             # how many recent chat messages the model sees
    "memories_in_prompt": 20,          # how many saved memories the model sees
    # --- microphone ---
    "mic_language": "en-US",
    "mic_auto_send": False,            # False = text goes into the box so you can check it
    "mic_privacy_acknowledged": False,
    # --- optional online AI (OFF until you configure it) ---
    "online_enabled": False,
    "online_base_url": "https://api.openai.com/v1",
    "online_model": "",                # you must choose a model name your provider offers
    "online_api_key": "",              # or set environment variable NIKO_ONLINE_API_KEY
    "online_timeout_seconds": 60,
    "settings_version": 2,
}


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _coerce_settings(s: dict) -> dict:
    """Fix wrong types so a hand-edited file can't crash the app."""
    d = DEFAULT_SETTINGS

    def num(key, lo, hi, as_int=False):
        try:
            v = float(s.get(key, d[key]))
        except (TypeError, ValueError):
            v = float(d[key])
        v = max(lo, min(hi, v))
        s[key] = int(v) if as_int else v

    num("window_alpha", 0.3, 1.0)
    num("gpt4all_timeout_seconds", 10, 1800, True)
    num("gpt4all_max_tokens", 16, 2048, True)
    num("gpt4all_temperature", 0.0, 2.0)
    num("history_messages", 0, 20, True)
    num("memories_in_prompt", 0, 100, True)
    num("online_timeout_seconds", 10, 600, True)
    for key in ("speak_replies", "always_on_top", "mic_auto_send",
                "mic_privacy_acknowledged", "online_enabled"):
        if not isinstance(s.get(key), bool):
            s[key] = d[key]
    for key in ("voice", "gpt4all_base_url", "gpt4all_model", "mic_language",
                "online_base_url", "online_model", "online_api_key", "ai_backend"):
        if not isinstance(s.get(key), str):
            s[key] = d[key]
    return s


def load_settings(path: Path = None) -> Tuple[dict, List[str]]:
    """Load settings.json, adding new keys without removing or changing old ones.

    Returns (settings, notes). Before the file is changed for the first time,
    a copy is saved as settings.json.bak-before-gpt4all.
    """
    path = path or SETTINGS_PATH
    notes: List[str] = []
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("settings file is not a JSON object")
            raw = parsed
        except Exception as exc:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            bad = path.with_name(f"{path.name}.corrupt-{stamp}")
            try:
                shutil.copy2(path, bad)
                notes.append(f"settings.json could not be read ({exc}). A copy was saved as {bad.name}; defaults are in use.")
            except Exception:
                notes.append(f"settings.json could not be read ({exc}); defaults are in use.")
            raw = {}
    merged = _coerce_settings({**DEFAULT_SETTINGS, **raw})
    if merged != raw:
        if raw and path.exists():
            backup = path.with_name(path.name + ".bak-before-gpt4all")
            if not backup.exists():
                try:
                    shutil.copy2(path, backup)
                except Exception:
                    pass
        try:
            _atomic_write_json(path, merged)
        except Exception as exc:
            notes.append(f"Could not save settings: {exc}")
    return merged, notes


def save_settings(settings: dict, path: Path = None) -> None:
    path = path or SETTINGS_PATH
    try:
        _atomic_write_json(path, settings)
    except Exception:
        pass  # a settings failure should never crash the app


# --------------------------------------------------------------------------
# Memory (the ORIGINAL tables: "memories" and "chat")
# --------------------------------------------------------------------------
class SchemaError(Exception):
    pass


REQUIRED_COLUMNS = {
    "memories": {"id", "content", "created_at"},
    "chat": {"id", "role", "content", "created_at"},
}


class Memory:
    def __init__(self, path: Path = None):
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self.lock = threading.Lock()
        # Same statements as the original. "IF NOT EXISTS" means an existing
        # table, with all its rows, is never touched.
        self.conn.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS chat (id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL)")
        self.conn.commit()
        self._verify_schema()

    def _verify_schema(self) -> None:
        for table, needed in REQUIRED_COLUMNS.items():
            cols = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            missing = needed - cols
            if missing:
                self.conn.close()
                raise SchemaError(
                    f"Your database table '{table}' is missing column(s): {', '.join(sorted(missing))}. "
                    "Niko stopped without changing anything."
                )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def remember(self, content: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO memories(content,created_at) VALUES(?,?)", (content.strip(), self._now()))
            self.conn.commit()

    def get_memories(self, limit: int = 35) -> List[str]:
        with self.lock:
            rows = self.conn.execute("SELECT content FROM memories ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in reversed(rows)]

    def all_memories(self) -> List[Tuple[int, str, str]]:
        with self.lock:
            return self.conn.execute("SELECT id, created_at, content FROM memories ORDER BY id").fetchall()

    def count_memories(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def save_chat(self, role: str, content: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO chat(role,content,created_at) VALUES(?,?,?)", (role, content, self._now()))
            self.conn.commit()

    def recent_chat(self, limit: int = 12) -> List[dict]:
        if limit <= 0:
            return []
        with self.lock:
            rows = self.conn.execute("SELECT role,content FROM chat ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def clear_chat(self) -> None:
        """Deletes the conversation history ONLY. The memories table is never touched."""
        with self.lock:
            self.conn.execute("DELETE FROM chat")
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.commit()
                self.conn.close()
            except Exception:
                pass


def backup_database(path: Path = None, keep: int = 10) -> Optional[Path]:
    """Copy the database into Backups/ (one copy per launch, newest 10 kept)."""
    path = Path(path or DB_PATH)
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dest = BACKUP_DIR / f"niko_memory-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        old = sorted(BACKUP_DIR.glob("niko_memory-*.sqlite3"))
        for f in old[:-keep]:
            try:
                f.unlink()
            except Exception:
                pass
        return dest
    except Exception:
        return None


# --------------------------------------------------------------------------
# Allow-lists (all original entries kept)
# --------------------------------------------------------------------------
SAFE_APPS = {
    "chrome": "Google Chrome", "google chrome": "Google Chrome",
    "safari": "Safari", "firefox": "Firefox", "whatsapp": "WhatsApp",
    "messages": "Messages", "mail": "Mail", "apple mail": "Mail",
    "notes": "Notes", "calendar": "Calendar", "finder": "Finder",
    "vscode": "Visual Studio Code", "vs code": "Visual Studio Code",
    "visual studio code": "Visual Studio Code", "terminal": "Terminal",
    "spotify": "Spotify", "photos": "Photos",
}

SITES = {
    "instagram": "https://www.instagram.com",
    "whatsapp web": "https://web.whatsapp.com",
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
}


def run_cmd(args: List[str], timeout: int = 20):
    """Run a command without a shell. (Tests replace this function.)"""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _clean_target(name: str) -> str:
    t = name.strip().rstrip(" .!?").lower()
    t = re.sub(r"\s+please$", "", t)
    t = re.sub(r"^the\s+", "", t)
    t = re.sub(r"\s+app$", "", t)
    return t.strip()


def open_app(name: str) -> Tuple[bool, str]:
    app = SAFE_APPS.get(_clean_target(name))
    if not app:
        return False, f"I don't have {name!r} on my approved app list yet."
    try:
        r = run_cmd(["open", "-a", app], timeout=15)
    except Exception as exc:
        return False, f"I couldn't open {app}: {exc}"
    if r.returncode != 0:
        detail = (r.stderr or "").strip().splitlines()
        extra = f" ({detail[-1]})" if detail else ""
        return False, f"I couldn't open {app}. Is it installed?{extra}"
    return True, f"Opening {app}."


def open_site(name: str) -> Optional[str]:
    key = _clean_target(name)
    url = SITES.get(key)
    if not url:
        return None
    try:
        r = run_cmd(["open", url], timeout=15)
    except Exception as exc:
        return f"I couldn't open {key.title()}: {exc}"
    if r.returncode != 0:
        return f"I couldn't open {key.title()}."
    return f"Opening {key.title()}."


def compose_email(to_addr: str, subject: str, body: str) -> str:
    """Create a DRAFT in Apple Mail. There is no 'send' command anywhere in this script."""
    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    script = "\n".join([
        'tell application "Mail"',
        'activate',
        f'set newMessage to make new outgoing message with properties {{subject:"{esc(subject)}", content:"{esc(body)}", visible:true}}',
        'tell newMessage',
        f'make new to recipient at end of to recipients with properties {{address:"{esc(to_addr)}"}}',
        'end tell',
        'end tell',
    ])
    r = run_cmd(["osascript", "-e", script], timeout=60)
    if r.returncode != 0:
        err = (r.stderr or "").strip() or "unknown AppleScript error"
        hint = ""
        if "-1743" in err or "not allowed" in err.lower():
            hint = (" macOS blocked Niko from controlling Mail. Open System Preferences > Security & Privacy > "
                    "Privacy > Automation and allow Terminal to control Mail.")
        raise RuntimeError(err + hint)
    return "I've opened the email as a draft in Apple Mail. Review it and press Send yourself."


# --------------------------------------------------------------------------
# Talking to AI servers
# --------------------------------------------------------------------------
class AIError(Exception):
    """An error with a message that is safe to show to the user."""


class _HttpFail(Exception):
    def __init__(self, kind: str, detail: str = "", code: int = 0):
        super().__init__(detail)
        self.kind, self.detail, self.code = kind, detail, code


def _http_json(url: str, payload=None, headers=None, timeout: float = 10, bypass_proxy: bool = False):
    data = None
    hdrs = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if bypass_proxy else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        raise _HttpFail("http", body, exc.code)
    except (socket.timeout, TimeoutError):
        raise _HttpFail("timeout")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise _HttpFail("timeout")
        raise _HttpFail("offline", str(exc.reason))
    except (ConnectionError, OSError) as exc:
        raise _HttpFail("offline", str(exc))
    try:
        return json.loads(raw)
    except ValueError:
        raise _HttpFail("bad", raw[:200])


def _extract_reply(result) -> str:
    try:
        choice = result["choices"][0]
        if isinstance(choice.get("message"), dict):
            return (choice["message"].get("content") or "").strip()
        return (choice.get("text") or "").strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIError("The AI server answered in a format Niko doesn't understand.")


class Gpt4AllClient:
    """Talks to GPT4All's Local API Server (OpenAI-compatible)."""

    def __init__(self, settings: dict):
        self.s = settings
        self._model: Optional[str] = None

    @property
    def base(self) -> str:
        return str(self.s.get("gpt4all_base_url") or DEFAULT_SETTINGS["gpt4all_base_url"]).rstrip("/")

    def _offline_message(self) -> str:
        return (f"I can't reach GPT4All at {self.base}.\n"
                "1. Open the GPT4All app.\n"
                "2. Go to Settings > Application and turn ON \"Enable Local API Server\" (port 4891).\n"
                "3. Make sure your Phi-3 model is downloaded, then try again.\n"
                "Click \"Setup help\" for more.")

    def list_models(self) -> List[str]:
        try:
            result = _http_json(self.base + "/models", timeout=8, bypass_proxy=True)
        except _HttpFail as exc:
            if exc.kind == "offline":
                raise AIError(self._offline_message())
            if exc.kind == "timeout":
                raise AIError("GPT4All is running but didn't answer the model list in time. Wait a moment and try again.")
            if exc.kind == "http":
                raise AIError(f"Something answered at {self.base}/models with error {exc.code}. "
                              "Check that the port is GPT4All's (4891).")
            raise AIError("The server at that address doesn't look like GPT4All (unexpected reply).")
        items = result.get("data") if isinstance(result, dict) else result
        ids: List[str] = []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    ids.append(str(it["id"]))
                elif isinstance(it, str):
                    ids.append(it)
        if not ids:
            raise AIError("GPT4All's server is running but reports no models. Open GPT4All, make sure Phi-3 Mini "
                          "Instruct is downloaded, and select it once in the chat window.")
        return ids

    def pick_model(self, ids: List[str]) -> str:
        wanted = str(self.s.get("gpt4all_model") or "").strip()
        if wanted:
            if wanted in ids:
                return wanted
            lowered = {i.lower(): i for i in ids}
            if wanted.lower() in lowered:
                return lowered[wanted.lower()]
            raise AIError(f"Your settings ask for the model {wanted!r}, but GPT4All reports: {', '.join(ids)}. "
                          "Fix \"gpt4all_model\" in settings.json, or set it to \"\" to auto-detect.")
        for i in ids:
            if "phi" in i.lower():
                return i
        return ids[0]

    def ensure_model(self, force: bool = False) -> str:
        if self._model and not force:
            return self._model
        self._model = self.pick_model(self.list_models())
        return self._model

    def check(self) -> str:
        ids = self.list_models()
        model = self.pick_model(ids)
        self._model = model
        return f"Connected to GPT4All at {self.base}.\nUsing model: {model}\nModels reported: {', '.join(ids)}"

    def chat(self, messages: List[dict]) -> str:
        timeout = int(self.s.get("gpt4all_timeout_seconds", 240))
        for attempt in (1, 2):
            model = self.ensure_model(force=(attempt == 2))
            payload = {
                "model": model, "messages": messages, "stream": False,
                "max_tokens": int(self.s.get("gpt4all_max_tokens", 400)),
                "temperature": float(self.s.get("gpt4all_temperature", 0.65)),
            }
            try:
                result = _http_json(self.base + "/chat/completions", payload, timeout=timeout, bypass_proxy=True)
            except _HttpFail as exc:
                if exc.kind == "offline":
                    self._model = None
                    raise AIError(self._offline_message())
                if exc.kind == "timeout":
                    raise AIError(f"The model didn't finish within {timeout} seconds. Phi-3 can be slow on an older Mac. "
                                  "Try a shorter question, or raise \"gpt4all_timeout_seconds\" in settings.json.")
                if exc.kind == "http":
                    if attempt == 1 and exc.code in (400, 404):
                        self._model = None
                        continue  # model list may have changed; rediscover once
                    raise AIError(f"GPT4All returned error {exc.code}. {exc.detail}".strip())
                raise AIError("GPT4All sent a reply Niko couldn't read.")
            reply = _extract_reply(result)
            if not reply:
                raise AIError("The model returned an empty answer. Try asking again.")
            return reply
        raise AIError("GPT4All rejected the request. Open GPT4All and make sure your Phi-3 model is loaded.")


class OnlineProvider:
    """Optional online AI (any OpenAI-compatible service). OFF unless you configure it.

    Niko never contacts it unless: online_enabled is true, you provided an API key and
    a model name, you typed an 'ask online' command, AND you click Yes on a confirmation.
    """

    def __init__(self, settings: dict):
        self.s = settings

    def api_key(self) -> str:
        return (os.environ.get("NIKO_ONLINE_API_KEY") or str(self.s.get("online_api_key") or "")).strip()

    def host(self) -> str:
        return urlparse(str(self.s.get("online_base_url") or "")).netloc or "the online provider"

    def configured(self) -> bool:
        return bool(self.s.get("online_enabled") and self.api_key() and str(self.s.get("online_model") or "").strip())

    def not_configured_message(self) -> str:
        return ("Online mode isn't set up, so nothing was sent anywhere. To use it, add to settings.json: "
                "\"online_enabled\": true, \"online_model\": \"<a model your provider offers>\", and your API key "
                "(best: set the environment variable NIKO_ONLINE_API_KEY). See NIKO_README.md.")

    def chat(self, question: str) -> str:
        if not self.configured():
            raise AIError(self.not_configured_message())
        base = str(self.s["online_base_url"]).rstrip("/")
        payload = {
            "model": self.s["online_model"],
            "messages": [
                {"role": "system", "content": "You are Niko, a helpful, honest assistant. Answer carefully and say when you are unsure."},
                {"role": "user", "content": question},
            ],
        }
        try:
            result = _http_json(base + "/chat/completions", payload,
                                headers={"Authorization": "Bearer " + self.api_key()},
                                timeout=int(self.s.get("online_timeout_seconds", 60)))
        except _HttpFail as exc:
            if exc.kind == "offline":
                raise AIError("Couldn't reach the online provider. Check your internet connection.")
            if exc.kind == "timeout":
                raise AIError("The online provider took too long to answer.")
            if exc.kind == "http":
                if exc.code in (401, 403):
                    raise AIError("The online provider rejected your API key.")
                raise AIError(f"The online provider returned error {exc.code}. {exc.detail}".strip())
            raise AIError("The online provider sent a reply Niko couldn't read.")
        reply = _extract_reply(result)
        if not reply:
            raise AIError("The online provider returned an empty answer.")
        return reply


# --------------------------------------------------------------------------
# Prompt building
# --------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are Niko, a friendly personal desktop assistant. Be honest about limitations. "
    "Never claim an action happened unless confirmed. The app handles a small allow-list "
    "of safe Mac commands. Be natural and concise."
)


def normalize_history(history: List[dict]) -> List[dict]:
    """Make the history strictly user/assistant/user/..., which chat templates need."""
    out: List[dict] = []
    for m in history:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        content = content[:700]
        if not out and role != "user":
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + content
        else:
            out.append({"role": role, "content": content})
    if out and out[-1]["role"] == "user":
        out.pop()  # the new user message follows, so roles keep alternating
    return out


def build_messages(memory: Memory, settings: dict, user_text: str) -> List[dict]:
    system = SYSTEM_PROMPT
    saved = memory.get_memories(int(settings.get("memories_in_prompt", 20)))
    if saved:
        system += "\n\nSaved user memories:\n- " + "\n- ".join(x[:300] for x in saved)
    history = normalize_history(memory.recent_chat(int(settings.get("history_messages", 6))))
    return [{"role": "system", "content": system}] + history + [{"role": "user", "content": user_text}]


# --------------------------------------------------------------------------
# Commands (no Tkinter in here, so they are safe to run in a background thread)
# --------------------------------------------------------------------------
@dataclass
class Confirm:
    title: str
    message: str
    action: Callable[[], str]
    declined_text: str


@dataclass
class Result:
    text: str = ""
    clear_display: bool = False
    no_log: bool = False        # don't save this exchange in the chat table
    is_error: bool = False      # don't save or speak error messages
    confirm: Optional[Confirm] = None
    ai_status: Optional[str] = None


class Services:
    def __init__(self, memory: Memory, settings: dict):
        self.memory = memory
        self.settings = settings
        self.ai = Gpt4AllClient(settings)
        self.online = OnlineProvider(settings)


HELP_TEXT = (
    "Things you can say:\n"
    "• open Chrome / open WhatsApp / open VS Code ...\n"
    "• open YouTube / Gmail / GitHub / Instagram ...\n"
    "• search for <something>   (or: google <something>)\n"
    "• remember that <something>\n"
    "• what do you remember about me\n"
    "• clear chat   (clears the conversation only, never memories)\n"
    "• email name@example.com | Subject | Message   (asks first, only makes a draft)\n"
    "• ask online: <question>   (only if you set up online mode)\n"
    "• check ai   (tests the GPT4All connection)\n"
    "Anything else goes to your local Phi-3 model."
)


def local_ai_reply(svc: Services, text: str) -> str:
    return svc.ai.chat(build_messages(svc.memory, svc.settings, text))


def handle_command(text: str, svc: Services) -> Optional[Result]:
    """Return a Result if `text` is a command, or None to send it to the AI."""
    t = text.strip()
    low = t.lower()
    low_plain = low.rstrip(" ?!.")

    m = re.match(r"^(?:niko,?\s*)?(?:remember that|remember)\s+(.+)$", t, re.I | re.S)
    if m:
        svc.memory.remember(m.group(1))
        return Result("I'll keep that in Niko's local memory.")

    if low_plain in {"what do you remember about me", "what do you know about me", "what do you remember",
                     "show memory", "show memories", "show my memories", "list memories"}:
        items = svc.memory.get_memories(200)
        return Result("Here's what I've saved:\n" + ("\n".join("• " + x for x in items) if items
                                                      else "Nothing saved yet. Say: remember that ..."))

    if low_plain in {"clear chat", "clear conversation"}:
        svc.memory.clear_chat()
        return Result("Chat history cleared. Saved memories are still here.", clear_display=True, no_log=True)

    if low_plain in {"help", "commands", "what can you do"}:
        return Result(HELP_TEXT)

    if low_plain in {"check ai", "check connection", "ai status", "test ai"}:
        try:
            info = svc.ai.check()
            return Result(info, ai_status="AI: ready")
        except AIError as exc:
            return Result(str(exc), is_error=True, ai_status="AI: offline")

    m = re.match(r"^(?:niko,?\s*)?(?:ask online|go online|online mode|online:)\s*[:,\-]?\s*(.+)$", t, re.I | re.S)
    if m:
        question = m.group(1).strip()
        if not svc.online.configured():
            return Result(svc.online.not_configured_message(), is_error=True)
        return Result(confirm=Confirm(
            "Niko - confirm online question",
            f"Send this question to {svc.online.host()} using your API key?\n\n"
            f"\"{question[:300]}\"\n\nNiko's saved memories and chat history are NOT sent.",
            lambda q=question: svc.online.chat(q),
            "Okay, I didn't send anything online."))

    m = re.match(r"^(?:niko,?\s*)?(?:please\s+)?(open|launch|start)\s+(.+)$", t, re.I)
    if m:
        verb, raw_target = m.group(1).lower(), m.group(2)
        target = _clean_target(raw_target)
        if target in SITES:
            return Result(open_site(target))
        if target == "whatsapp":
            ok, msg = open_app("whatsapp")
            if not ok:
                return Result(open_site("whatsapp web") or msg)
            return Result(msg)
        if verb == "start" and target not in SAFE_APPS:
            return None  # "start a story about..." is normal chat, not an app command
        ok, msg = open_app(target)
        return Result(msg)

    m = re.match(r"^(?:search(?: the web)? for|google)\s+(.+)$", t, re.I)
    if m:
        try:
            r = run_cmd(["open", "https://www.google.com/search?q=" + quote(m.group(1).strip())], timeout=15)
            if r.returncode != 0:
                return Result("I couldn't open the browser for that search.")
        except Exception as exc:
            return Result(f"I couldn't open the browser for that search: {exc}")
        return Result("I opened that Google search.")

    m = re.match(r"^email\s+([^|]+)\|([^|]+)\|(.+)$", t, re.I | re.S)
    if m:
        to_addr, subject, body = [x.strip() for x in m.groups()]
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to_addr):
            return Result("That email address doesn't look valid. Please check it.")
        preview = body if len(body) <= 300 else body[:300] + "..."
        return Result(confirm=Confirm(
            "Niko - confirm email",
            f"Open a draft addressed to:\n{to_addr}\n\nSubject: {subject}\n\nMessage:\n{preview}\n\n"
            "Niko will NOT send it automatically. Continue?",
            lambda: compose_email(to_addr, subject, body),
            "Okay, I didn't create the email."))

    return None


# --------------------------------------------------------------------------
# Voice output (macOS "say") - runs as a separate process, never blocks the window
# --------------------------------------------------------------------------
class Speaker:
    def __init__(self, settings: dict):
        self.s = settings
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()

    @staticmethod
    def clean(text: str) -> str:
        t = re.sub(r"https?://\S+", "link", text)
        t = re.sub(r"[*_`#•]+", " ", t)
        return t[:1200]

    def speak(self, text: str) -> None:
        threading.Thread(target=self._run, args=(self.clean(text),), daemon=True).start()

    def _run(self, text: str) -> None:
        try:
            with self.lock:
                self._stop_locked()
                args = ["say"]
                voice = str(self.s.get("voice") or "").strip()
                if voice and re.match(r"^[\w][\w .()\-]*$", voice):
                    args += ["-v", voice]
                args += ["-f", "-"]  # read text from stdin so text starting with "-" is safe
                self.proc = subprocess.Popen(args, stdin=subprocess.PIPE,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.proc.stdin.write(text.encode("utf-8", "replace"))
                self.proc.stdin.close()
        except Exception:
            pass  # speech problems must never crash Niko

    def _stop_locked(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()


# --------------------------------------------------------------------------
# Voice input (optional). Press once to start, press again to stop.
# --------------------------------------------------------------------------
class MicError(Exception):
    pass


class MicRecorder:
    MAX_SECONDS = 30

    def __init__(self):
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def record_and_transcribe(self, language: str = "en-US", on_captured: Callable[[], None] = None) -> str:
        try:
            import speech_recognition as sr
        except ImportError:
            raise MicError("Microphone support isn't installed. In Terminal run: "
                           "pip3 install -r requirements-microphone.txt   (see NIKO_README.md, step 'Microphone').")
        self.stop_event.clear()
        try:
            source = sr.Microphone()
        except AttributeError:
            raise MicError("PyAudio isn't installed, so Niko can't use the microphone. "
                           "In Terminal run: brew install portaudio && pip3 install pyaudio")
        except OSError:
            raise MicError("No microphone was found. Check that one is available in System Preferences > Sound > Input.")
        frames: List[bytes] = []
        try:
            with source as src:
                started = time.time()
                while not self.stop_event.is_set() and time.time() - started < self.MAX_SECONDS:
                    frames.append(src.stream.read(src.CHUNK))
                rate, width = src.SAMPLE_RATE, src.SAMPLE_WIDTH
        except OSError as exc:
            raise MicError(f"The microphone couldn't be opened: {exc}. Check System Preferences > Security & Privacy > "
                           "Privacy > Microphone and allow Terminal (or your Python app).")
        if on_captured:
            on_captured()
        if not frames:
            raise MicError("Nothing was recorded.")
        recognizer = sr.Recognizer()
        audio = sr.AudioData(b"".join(frames), rate, width)
        try:
            return recognizer.recognize_google(audio, language=language).strip()
        except sr.UnknownValueError:
            raise MicError("I couldn't understand that. Speak a little closer or louder, and check that Terminal is "
                           "allowed to use the microphone in System Preferences > Security & Privacy > Privacy.")
        except sr.RequestError as exc:
            raise MicError(f"The speech recognition service couldn't be reached ({exc}). "
                           "Voice input needs an internet connection.")


MIC_PRIVACY_NOTICE = (
    "Voice input uses Google's online speech recognition (recognize_google).\n\n"
    "When you record, the audio is SENT TO GOOGLE over the internet to be turned into text. "
    "This is NOT offline.\n\n"
    "Niko only records after you press the Mic button, and stops when you press it again "
    "(or after 30 seconds).\n\nContinue?"
)

SETUP_HELP_TEXT = (
    "Local AI (GPT4All + Phi-3 Mini):\n"
    "1. Open the GPT4All app and make sure Phi-3 Mini Instruct is downloaded.\n"
    "2. GPT4All > Settings > Application > turn ON \"Enable Local API Server\" (port 4891).\n"
    "3. Keep GPT4All open, then talk to Niko. Type \"check ai\" to test.\n\n"
    "Microphone (optional): see NIKO_README.md. Audio is sent to Google for recognition.\n\n"
    "Memory location:\n~/Library/Application Support/Niko/\n"
    "Automatic backups: the Backups folder inside it.\n\n"
    "Type \"help\" to see all commands."
)

READY = "PERSONAL ASSISTANT  \u2022  READY"


def _tk_safe(text: str) -> str:
    """Old Tk (8.5) crashes on emoji; remove characters it can't draw."""
    if tk is not None and getattr(tk, "TkVersion", 9.0) < 8.6:
        return "".join(ch for ch in text if ord(ch) <= 0xFFFF)
    return text


# --------------------------------------------------------------------------
# The window.  Rules: widgets are touched ONLY on the main thread.
# Background threads put work on self.ui_queue; the main thread runs it from a
# root.after() poller.
# --------------------------------------------------------------------------
class NikoApp:
    BG = "#090d14"

    def __init__(self, root, svc: Services, startup_notes: List[str] = None):
        self.root = root
        self.svc = svc
        self.s = svc.settings
        self.speaker = Speaker(self.s)
        self.recorder = MicRecorder()
        self.ui_queue: "queue.Queue" = queue.Queue()
        self.busy = False
        self.mic_state = "idle"  # idle | listening | transcribing
        self.closing = False

        root.title("Niko")
        root.geometry("430x620+80+100")
        root.minsize(380, 460)
        try:
            root.attributes("-topmost", bool(self.s.get("always_on_top", True)))
            root.attributes("-alpha", float(self.s["window_alpha"]))
        except Exception:
            pass
        root.configure(bg=self.BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        header = tk.Frame(root, bg=self.BG)
        header.pack(fill="x", padx=18, pady=(14, 8))
        orb = tk.Canvas(header, width=42, height=42, bg=self.BG, highlightthickness=0)
        orb.pack(side="left")
        orb.create_oval(4, 4, 38, 38, fill="#102a3a", outline="#35d9ff", width=2)
        orb.create_oval(13, 13, 29, 29, fill="#35d9ff", outline="")
        titlebox = tk.Frame(header, bg=self.BG)
        titlebox.pack(side="left", padx=10)
        tk.Label(titlebox, text="NIKO", fg="#eaf8ff", bg=self.BG, font=("Helvetica", 16, "bold")).pack(anchor="w")
        self.status = tk.Label(titlebox, text=READY, fg="#66b9d0", bg=self.BG, font=("Helvetica", 8))
        self.status.pack(anchor="w")
        self.ai_label = tk.Label(header, text="AI: checking...", fg="#8aa3ad", bg=self.BG, font=("Helvetica", 9))
        self.ai_label.pack(side="right")

        chat_frame = tk.Frame(root, bg=self.BG)
        chat_frame.pack(fill="both", expand=True, padx=14, pady=8)
        scrollbar = tk.Scrollbar(chat_frame)
        scrollbar.pack(side="right", fill="y")
        self.chat = tk.Text(chat_frame, bg="#0e1520", fg="#e8f1f7", insertbackground="white",
                            relief="flat", wrap="word", font=("Helvetica", 11), padx=13, pady=12,
                            state="disabled", yscrollcommand=scrollbar.set)
        self.chat.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.chat.yview)
        self.chat.tag_config("you", foreground="#6edbff", font=("Helvetica", 11, "bold"))
        self.chat.tag_config("niko", foreground="#9cf0c7", font=("Helvetica", 11, "bold"))
        self.chat.tag_config("body", foreground="#e8f1f7")

        bottom = tk.Frame(root, bg=self.BG)
        bottom.pack(fill="x", padx=14, pady=(4, 8))
        self.entry = tk.Entry(bottom, bg="#172333", fg="#f4fbff", insertbackground="white",
                              relief="flat", font=("Helvetica", 12))
        self.entry.pack(side="left", fill="x", expand=True, ipady=11, padx=(0, 7))
        self.entry.bind("<Return>", lambda event: self.send())
        self.send_btn = self.make_button(bottom, "SEND", self.send, "#147e9b", "#1a9dbd", "white", bold=True)
        self.send_btn.pack(side="right")

        controls = tk.Frame(root, bg=self.BG)
        controls.pack(fill="x", padx=14, pady=(0, 12))
        self.mic_btn = self.make_button(controls, "Mic", self.toggle_mic, "#172333", "#22344a")
        self.mic_btn.pack(side="left")
        self.voice_btn = self.make_button(controls, self.voice_label(), self.toggle_speech, "#172333", "#22344a")
        self.voice_btn.pack(side="left", padx=6)
        self.make_button(controls, "Memory", self.show_memory, "#172333", "#22344a").pack(side="left")
        self.make_button(controls, "Setup help", self.show_help, "#172333", "#22344a").pack(side="right")

        self.add_message("Niko", "Hey. I'm Niko. Try \u201copen Chrome\u201d or \u201cremember that ...\u201d. "
                                 "Type \u201chelp\u201d to see everything I can do. I chat using your local Phi-3 model through GPT4All.")
        for note in (startup_notes or []):
            self.add_message("Niko", note)
        self.entry.focus_set()
        self.root.after(60, self.poll_queue)
        threading.Thread(target=self.startup_check, daemon=True).start()

    # ---- helpers that run on the main thread only ----
    def make_button(self, parent, text, command, bg, hover, fg="#cce8f1", bold=False):
        font = ("Helvetica", 11, "bold") if bold else ("Helvetica", 11)
        label = tk.Label(parent, text=text, bg=bg, fg=fg, padx=12, pady=7, font=font, cursor="pointinghand")
        label._base_bg = bg
        label._hover_bg = hover
        label._enabled = True
        label.bind("<Button-1>", lambda event: command() if label._enabled else None)
        label.bind("<Enter>", lambda event: label.config(bg=label._hover_bg if label._enabled else label._base_bg))
        label.bind("<Leave>", lambda event: label.config(bg=label._base_bg))
        return label

    def post(self, fn, *args) -> None:
        """Called from ANY thread. Queues work for the main thread."""
        self.ui_queue.put((fn, args))

    def poll_queue(self) -> None:
        try:
            while True:
                fn, args = self.ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception as exc:  # keep the window alive
                    try:
                        self.add_message("Niko", f"Something went wrong in the window: {exc}")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(60, self.poll_queue)

    def set_status(self, text: str) -> None:
        self.status.config(text=text)

    def set_ai_label(self, text: str) -> None:
        self.ai_label.config(text=text, fg="#9cf0c7" if text.endswith("ready") else "#e6a37a")

    def voice_label(self) -> str:
        return "Speech: ON" if self.s["speak_replies"] else "Speech: OFF"

    def add_message(self, who: str, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", who + "\n", "you" if who == "You" else "niko")
        self.chat.insert("end", _tk_safe(text) + "\n\n", "body")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def clear_display(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")

    def idle_status(self) -> str:
        return READY

    # ---- startup AI check (background) ----
    def startup_check(self) -> None:
        try:
            self.svc.ai.check()
            self.post(self.set_ai_label, "AI: ready")
        except AIError:
            self.post(self.set_ai_label, "AI: offline")
        except Exception:
            self.post(self.set_ai_label, "AI: offline")

    # ---- sending ----
    def send(self) -> None:
        if self.busy or self.mic_state != "idle":
            return
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.add_message("You", text)
        self.busy = True
        self.speaker.stop()
        self.set_status("THINKING\u2026")
        threading.Thread(target=self.process, args=(text,), daemon=True).start()

    def process(self, text: str) -> None:
        """Background thread. Must not touch any widget or dialog."""
        try:
            result = handle_command(text, self.svc)
            if result is None:
                try:
                    reply = local_ai_reply(self.svc, text)
                    result = Result(reply, ai_status="AI: ready")
                except AIError as exc:
                    result = Result(str(exc), is_error=True, ai_status="AI: offline")
        except Exception as exc:
            result = Result(f"Something went wrong: {exc}", is_error=True)
        if result.confirm is not None:
            self.post(self.ask_confirm, text, result)
        else:
            self.post(self.finish, text, result)

    def ask_confirm(self, user_text: str, result: Result) -> None:
        """Main thread. Shows the yes/no dialog."""
        c = result.confirm
        approved = messagebox.askyesno(c.title, c.message, parent=self.root)
        if not approved:
            self.finish(user_text, Result(c.declined_text))
            return
        self.set_status("WORKING\u2026")
        threading.Thread(target=self.run_confirmed, args=(user_text, c), daemon=True).start()

    def run_confirmed(self, user_text: str, c: Confirm) -> None:
        """Background thread: performs the action the user just approved."""
        try:
            result = Result(c.action())
        except AIError as exc:
            result = Result(str(exc), is_error=True)
        except Exception as exc:
            result = Result(f"I couldn't do that: {exc}", is_error=True)
        self.post(self.finish, user_text, result)

    def finish(self, user_text: str, result: Result) -> None:
        """Main thread."""
        if not result.no_log and not result.is_error:
            try:
                self.svc.memory.save_chat("user", user_text)
                self.svc.memory.save_chat("assistant", result.text)
            except Exception as exc:
                result.text += f"\n(I couldn't save this to the chat history: {exc})"
        if result.clear_display:
            self.clear_display()
        self.add_message("Niko", result.text or "(no reply)")
        if result.ai_status:
            self.set_ai_label(result.ai_status)
        self.set_status(READY)
        self.busy = False
        if self.s["speak_replies"] and not result.is_error and not self.closing:
            self.speaker.speak(result.text)

    # ---- speech output toggle ----
    def toggle_speech(self) -> None:
        self.s["speak_replies"] = not self.s["speak_replies"]
        save_settings(self.s)
        self.voice_btn.config(text=self.voice_label())
        if not self.s["speak_replies"]:
            self.speaker.stop()

    # ---- microphone ----
    def toggle_mic(self) -> None:
        if self.mic_state == "listening":
            self.recorder.stop()
            self.mic_state = "transcribing"
            self.mic_btn.config(text="Working...", bg="#172333")
            self.mic_btn._enabled = False
            self.set_status("TRANSCRIBING\u2026")
            return
        if self.mic_state != "idle" or self.busy:
            return
        if not self.s.get("mic_privacy_acknowledged"):
            if not messagebox.askokcancel("Niko - voice input and privacy", MIC_PRIVACY_NOTICE, parent=self.root):
                return
            self.s["mic_privacy_acknowledged"] = True
            save_settings(self.s)
        self.speaker.stop()
        self.mic_state = "listening"
        self.mic_btn.config(text="\u25cf Stop", bg="#9b2f3f")
        self.mic_btn._base_bg = "#9b2f3f"
        self.set_status("LISTENING\u2026 press Stop when done")
        threading.Thread(target=self.mic_worker, daemon=True).start()

    def mic_worker(self) -> None:
        """Background thread: records and transcribes. No widget access."""
        try:
            text = self.recorder.record_and_transcribe(
                str(self.s.get("mic_language") or "en-US"),
                on_captured=lambda: self.post(self.set_status, "TRANSCRIBING\u2026"))
            self.post(self.mic_done, text, None)
        except MicError as exc:
            self.post(self.mic_done, None, str(exc))
        except Exception as exc:
            self.post(self.mic_done, None, f"Voice input failed: {exc}")

    def mic_done(self, text: Optional[str], error: Optional[str]) -> None:
        """Main thread."""
        self.mic_state = "idle"
        self.mic_btn.config(text="Mic", bg="#172333")
        self.mic_btn._base_bg = "#172333"
        self.mic_btn._enabled = True
        self.set_status(READY if not self.busy else "THINKING\u2026")
        if error:
            self.add_message("Niko", error)
            return
        if not text:
            self.add_message("Niko", "I didn't catch anything.")
            return
        existing = self.entry.get().strip()
        self.entry.delete(0, "end")
        self.entry.insert(0, (existing + " " + text).strip())
        if self.s.get("mic_auto_send"):
            self.send()
        else:
            self.entry.focus_set()

    # ---- memory window and help ----
    def show_memory(self) -> None:
        rows = self.svc.memory.all_memories()
        win = tk.Toplevel(self.root)
        win.title("Niko memory")
        win.geometry("420x360+120+140")
        win.configure(bg=self.BG)
        win.transient(self.root)
        box = tk.Text(win, bg="#0e1520", fg="#e8f1f7", relief="flat", wrap="word",
                      font=("Helvetica", 11), padx=12, pady=10)
        box.pack(fill="both", expand=True, padx=10, pady=10)
        if rows:
            lines = [f"\u2022 {content}\n   saved {created}" for _id, created, content in rows]
            box.insert("end", f"{len(rows)} saved memories\n\n" + "\n\n".join(_tk_safe(x) for x in lines))
        else:
            box.insert("end", "No saved memories yet.\nSay: remember that I prefer dark mode.")
        box.config(state="disabled")

    def show_help(self) -> None:
        messagebox.showinfo("Niko setup", SETUP_HELP_TEXT, parent=self.root)

    # ---- closing ----
    def on_close(self) -> None:
        self.closing = True
        try:
            self.recorder.stop()
            self.speaker.stop()
            self.svc.memory.close()
        finally:
            self.root.destroy()


def main() -> int:
    if tk is None:
        print("Niko needs Tkinter, which wasn't found. Install Python from https://www.python.org/downloads/macos/ and try again.")
        return 1
    settings, notes = load_settings()
    backup_database()
    try:
        memory = Memory()
    except SchemaError as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Niko - database problem", str(exc))
        return 1
    root = tk.Tk()
    NikoApp(root, Services(memory, settings), notes)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
