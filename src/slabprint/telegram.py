# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franz Sittampalam
"""Print whatever is sent to a Telegram bot.

Long-polls, so nothing listens on a port and no tunnel or port forwarding is
needed: the machine reaches out to Telegram, never the other way round. That
makes it usable from anywhere without exposing the printer to the internet.

The chat allowlist is mandatory and has no wildcard. A bot token is a bearer
credential and a bot's username is public, so without an allowlist anyone who
finds the bot could make paper come out of a printer in your house.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.telegram.org"

POLL_SECONDS = 50  # Telegram holds the request open; this is not a busy loop
MAX_CHARS = 2000  # more than the paper holds; refuse rather than crop silently
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024  # the bot API's own download ceiling


class Bridge:
    def __init__(self, token: str, allowed: set[int], printer, state_dir: Path):
        self.token = token
        self.allowed = allowed
        self.printer = printer  # callable(kind, payload) -> str
        self.offset_file = state_dir / "telegram-offset"
        self.state_dir = state_dir

    # ---- Telegram plumbing -------------------------------------------------

    def api(self, method: str, params: dict | None = None, timeout: int = 70):
        url = f"{API}/bot{self.token}/{method}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)

    def say(self, chat: int, text: str) -> None:
        try:
            self.api("sendMessage", {"chat_id": chat, "text": text}, timeout=30)
        except Exception as exc:  # a failed reply must never kill the bridge
            print(f"reply failed: {exc}", file=sys.stderr)

    def download(self, file_id: str) -> bytes:
        info = self.api("getFile", {"file_id": file_id}, timeout=60)["result"]
        url = f"{API}/file/bot{self.token}/{info['file_path']}"
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.read()

    # ---- message handling --------------------------------------------------

    def handle(self, message: dict) -> None:
        chat_info = message.get("chat") or {}
        chat = chat_info.get("id")
        if chat is None:
            return
        if chat not in self.allowed:
            print(f"ignoring chat {chat} (not in the allowlist)", file=sys.stderr)
            return

        # A group is a conversation between people, not an input queue: printing
        # every message would put the whole chat on paper. Groups therefore need
        # an explicit /print, which also means Telegram's default privacy mode
        # (a bot in a group sees only commands addressed to it) is never a problem.
        in_group = chat_info.get("type") in ("group", "supergroup")
        text = message.get("text") or ""
        caption = message.get("caption") or ""
        trigger = text or caption
        command, rest = ("", "")
        if trigger.startswith("/"):
            head, _, rest = trigger.partition(" ")
            command, rest = head.lstrip("/").split("@")[0].lower(), rest.strip()

        if command in ("start", "help"):
            self.say(chat, self.usage(in_group))
            return
        if command == "status":
            self.say(chat, self.printer("status", None))
            return
        if in_group and command != "print":
            return  # not addressed to us; stay out of the conversation

        body = rest if command == "print" else (text or caption)

        if document := message.get("document"):
            self.print_document(chat, document, body if command == "print" else caption)
            return
        if photos := message.get("photo"):
            self.say(chat, self.printer("image", self.download(photos[-1]["file_id"])))
            if body:
                self.say(chat, self.printer("text", body))
            return

        if not body.strip():
            self.say(chat, "nothing to print — send text, a photo, or a PDF")
            return
        if len(body) > MAX_CHARS:
            self.say(chat, f"that is {len(body)} characters; the paper holds about {MAX_CHARS}")
            return
        self.say(chat, self.printer("text", body))

    def print_document(self, chat: int, document: dict, caption: str) -> None:
        mime = document.get("mime_type") or ""
        size = document.get("file_size") or 0
        if size > MAX_DOCUMENT_BYTES:
            self.say(chat, "that file is too large for Telegram to hand me")
            return
        if mime == "application/pdf" or (document.get("file_name") or "").lower().endswith(".pdf"):
            kind = "pdf"
        elif mime.startswith("image/"):
            kind = "image"
        else:
            self.say(chat, f"I can print text, images and PDFs — not {mime or 'that'}")
            return
        self.say(chat, self.printer(kind, self.download(document["file_id"])))
        if caption:
            self.say(chat, self.printer("text", caption))

    @staticmethod
    def usage(in_group: bool) -> str:
        if in_group:
            return (
                "/print <text> to print text.\n"
                "Send a photo or PDF captioned /print to print that.\n"
                "/status to check the printer."
            )
        return "Send me text, a photo or a PDF and I print it.\n/status to check the printer."

    # ---- the loop ----------------------------------------------------------

    def read_offset(self) -> int:
        try:
            return int(self.offset_file.read_text().strip())
        except (OSError, ValueError):
            return 0

    def write_offset(self, value: int) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.offset_file.write_text(str(value))

    def run(self) -> int:
        offset = self.read_offset()
        print(f"slabprint telegram: polling, {len(self.allowed)} chat(s) allowed", file=sys.stderr)
        while True:
            try:
                payload = self.api("getUpdates", {"offset": offset, "timeout": POLL_SECONDS})
            except urllib.error.HTTPError as exc:
                # 409 means something else polls this bot. Telegram delivers each
                # update once, so the two would steal messages from each other.
                hint = " — another process is polling this bot" if exc.code == 409 else ""
                print(f"telegram HTTP {exc.code}{hint}; backing off", file=sys.stderr)
                time.sleep(15)
                continue
            except Exception as exc:
                print(f"poll failed ({type(exc).__name__}); retrying", file=sys.stderr)
                time.sleep(10)
                continue

            for update in payload.get("result", []):
                offset = update["update_id"] + 1
                self.write_offset(offset)
                message = update.get("message") or update.get("channel_post")
                if not message:
                    continue
                try:
                    self.handle(message)
                except Exception as exc:  # one bad message must not stop the bridge
                    print(f"handling failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def resolve_token() -> str:
    """Token from the environment, or from a command that prints one.

    The command indirection exists so the token can live in a password manager
    rather than in a plist or a shell history.
    """
    if value := os.environ.get("SLABPRINT_TG_TOKEN"):
        return value
    if command := os.environ.get("SLABPRINT_TG_TOKEN_COMMAND"):
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        raise SystemExit(f"SLABPRINT_TG_TOKEN_COMMAND failed: {result.stderr.strip()}")
    raise SystemExit(
        "set SLABPRINT_TG_TOKEN, or SLABPRINT_TG_TOKEN_COMMAND to a command printing it"
    )


def resolve_allowlist() -> set[int]:
    raw = os.environ.get("SLABPRINT_TG_ALLOW", "").strip()
    if not raw:
        raise SystemExit(
            "SLABPRINT_TG_ALLOW is required: comma-separated chat ids permitted to "
            "print. Run `slabprint telegram --whoami` to discover yours."
        )
    return {int(part) for part in raw.split(",") if part.strip()}


def whoami(token: str) -> int:
    """Report chat ids that have messaged the bot, to seed the allowlist."""
    url = f"{API}/bot{token}/getUpdates?" + urllib.parse.urlencode({"limit": 20, "timeout": 5})
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            updates = json.load(response).get("result", [])
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise SystemExit(
                "HTTP 409: another process is already polling this bot. Telegram "
                "delivers each update once, so give printing a bot of its own."
            ) from exc
        raise
    seen = {}
    for update in updates:
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if chat.get("id"):
            seen[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
    if not seen:
        print("no messages seen — send the bot something, then run this again")
        return 1
    for chat_id, who in seen.items():
        kind = "group" if chat_id < 0 else "direct"
        print(f"{chat_id}\t{kind}\t{who}")
    return 0
