# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franz Sittampalam
"""The Telegram bridge: who may print, and what triggers a print."""

from pathlib import Path

import pytest

from slabprint import telegram

MINE = 42
GROUP = -100123
STRANGER = 99999


@pytest.fixture
def bridge(tmp_path):
    printed, said = [], []

    def printer(kind, payload):
        printed.append((kind, payload))
        return "printed"

    instance = telegram.Bridge("token", {MINE, GROUP}, printer, Path(tmp_path))
    instance.say = lambda chat, text: said.append((chat, text))
    instance.download = lambda file_id: b"bytes-for-" + file_id.encode()
    instance.printed, instance.said = printed, said
    return instance


def direct(**message):
    return {"chat": {"id": MINE, "type": "private"}, **message}


def group(**message):
    return {"chat": {"id": GROUP, "type": "supergroup"}, **message}


# --- the allowlist ------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        {"chat": {"id": STRANGER, "type": "private"}, "text": "spam"},
        {"chat": {"id": -999, "type": "supergroup"}, "text": "/print spam"},
        {"chat": {"id": STRANGER, "type": "private"}, "photo": [{"file_id": "x"}]},
        {"chat": {"id": STRANGER, "type": "private"}, "document": {"mime_type": "application/pdf"}},
    ],
)
def test_a_chat_that_is_not_allowed_cannot_print(bridge, message):
    bridge.handle(message)
    assert bridge.printed == []


def test_a_stranger_gets_no_reply_either(bridge):
    """Silence rather than 'not authorised', which would confirm the bot is live."""
    bridge.handle({"chat": {"id": STRANGER, "type": "private"}, "text": "hello"})
    assert bridge.said == []


# --- direct messages ----------------------------------------------------------


def test_plain_text_prints(bridge):
    bridge.handle(direct(text="buy milk"))
    assert bridge.printed == [("text", "buy milk")]


def test_a_photo_prints(bridge):
    bridge.handle(direct(photo=[{"file_id": "small"}, {"file_id": "large"}]))
    # Telegram sends several sizes; the largest is the last.
    assert bridge.printed == [("image", b"bytes-for-large")]


def test_a_pdf_prints(bridge):
    bridge.handle(direct(document={"file_id": "d", "mime_type": "application/pdf"}))
    assert bridge.printed == [("pdf", b"bytes-for-d")]


def test_a_pdf_without_a_mime_type_is_recognised_by_name(bridge):
    bridge.handle(direct(document={"file_id": "d", "file_name": "Invoice.PDF"}))
    assert bridge.printed == [("pdf", b"bytes-for-d")]


def test_an_unprintable_document_is_refused_politely(bridge):
    bridge.handle(direct(document={"file_id": "d", "mime_type": "application/zip"}))
    assert bridge.printed == []
    assert "zip" in bridge.said[-1][1]


def test_an_over_large_document_is_refused(bridge):
    bridge.handle(
        direct(document={"file_id": "d", "mime_type": "application/pdf", "file_size": 10**9})
    )
    assert bridge.printed == []


def test_very_long_text_is_refused_rather_than_cropped(bridge):
    bridge.handle(direct(text="x" * (telegram.MAX_CHARS + 1)))
    assert bridge.printed == []
    assert "characters" in bridge.said[-1][1]


def test_empty_text_is_refused(bridge):
    bridge.handle(direct(text="   "))
    assert bridge.printed == []


# --- groups -------------------------------------------------------------------


def test_ordinary_group_chatter_is_ignored(bridge):
    """Otherwise the whole conversation ends up on paper."""
    bridge.handle(group(text="what shall we have for dinner"))
    assert bridge.printed == []
    assert bridge.said == []


def test_a_group_print_command_prints_the_remainder(bridge):
    bridge.handle(group(text="/print bins out"))
    assert bridge.printed == [("text", "bins out")]


def test_a_group_command_addressed_to_the_bot_works(bridge):
    bridge.handle(group(text="/print@slabprint_bot bins out"))
    assert bridge.printed == [("text", "bins out")]


def test_a_group_photo_needs_the_caption(bridge):
    bridge.handle(group(photo=[{"file_id": "p"}]))
    assert bridge.printed == []
    bridge.handle(group(photo=[{"file_id": "p"}], caption="/print"))
    assert bridge.printed == [("image", b"bytes-for-p")]


def test_a_group_pdf_needs_the_caption(bridge):
    document = {"file_id": "d", "mime_type": "application/pdf"}
    bridge.handle(group(document=document))
    assert bridge.printed == []
    bridge.handle(group(document=document, caption="/print"))
    assert bridge.printed == [("pdf", b"bytes-for-d")]


def test_status_works_in_both(bridge):
    bridge.handle(direct(text="/status"))
    bridge.handle(group(text="/status"))
    assert [kind for kind, _ in bridge.printed] == ["status", "status"]


def test_help_explains_the_group_rule(bridge):
    bridge.handle(group(text="/help"))
    assert "/print" in bridge.said[-1][1]
