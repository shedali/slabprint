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
