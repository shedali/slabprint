# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franz Sittampalam
"""Argument parsing. The commands themselves need a printer; the parser does not."""

import pytest

from slabprint import cli


def parse(argv):
    return cli.build_parser().parse_args(argv)


def test_print_takes_positional_text():
    assert parse(["print", "hello", "world"]).text == ["hello", "world"]


def test_print_defaults_to_the_automatic_transport():
    assert parse(["print", "x"]).transport == "auto"


def test_print_accepts_columns_and_size():
    args = parse(["print", "--columns", "2", "--size", "20", "x"])
    assert (args.columns, args.size) == (2, 20)


def test_an_unknown_transport_is_rejected():
    with pytest.raises(SystemExit):
        parse(["print", "--transport", "smoke-signal", "x"])


def test_schedule_requires_a_time():
    with pytest.raises(SystemExit):
        parse(["schedule", "--", "print", "x"])


def test_schedule_collects_the_remaining_command():
    args = parse(["schedule", "--at", "07:30", "--", "print", "Bins out"])
    assert args.at == "07:30"
    assert [token for token in args.rest if token != "--"] == ["print", "Bins out"]


def test_queue_defaults_to_listing():
    assert parse(["queue"]).action == "list"


@pytest.mark.parametrize("action", ["list", "run", "clear"])
def test_queue_actions(action):
    assert parse(["queue", action]).action == action


def test_an_unknown_queue_action_is_rejected():
    with pytest.raises(SystemExit):
        parse(["queue", "incinerate"])


def test_serve_has_a_default_port():
    assert parse(["serve"]).port == 8719


def test_a_command_is_required():
    with pytest.raises(SystemExit):
        parse([])


def test_entry_command_is_runnable():
    command = cli.entry_command()
    assert isinstance(command, list) and command


def test_bare_text_defaults_to_printing():
    assert cli.with_default_command(["hello"]) == ["print", "hello"]


def test_a_leading_flag_defaults_to_printing():
    assert cli.with_default_command(["--image", "x.png"]) == ["print", "--image", "x.png"]


@pytest.mark.parametrize("command", sorted(cli.subcommands()))
def test_a_real_subcommand_is_left_alone(command):
    assert cli.with_default_command([command, "x"]) == [command, "x"]


def test_help_is_left_alone():
    assert cli.with_default_command(["--help"]) == ["--help"]


def test_no_arguments_with_a_pipe_means_print_from_stdin(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.with_default_command([]) == ["print"]


def test_no_arguments_on_a_terminal_shows_help(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    assert cli.with_default_command([]) == []


def test_every_registered_subcommand_is_recognised():
    """Guards the shim against drifting when a command is added."""
    registered = cli.subcommands()
    assert {"print", "schedule", "queue", "serve", "status", "telegram"} <= registered
    for command in registered:
        assert cli.with_default_command([command]) == [command]


class FakeResult:
    def __init__(self, stdout=b"", returncode=0, stderr=b""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def test_the_clipboard_reader_uses_the_first_tool_installed(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: name == "wl-paste")
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: FakeResult(b"PNGDATA"))
    assert cli.read_clipboard_image() == b"PNGDATA"


def test_no_clipboard_tool_explains_what_to_install(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="pngpaste"):
        cli.read_clipboard_image()


def test_an_empty_clipboard_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: name == "pngpaste")
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: FakeResult(b"", 1, b"no image data"))
    with pytest.raises(ValueError, match="no image"):
        cli.read_clipboard_image()
