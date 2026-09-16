# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franz Sittampalam
"""slabprint — print to a MangoSlab nemonic MIP-001 sticky-note printer.

  slabprint print "Buy milk"                  print a line of text
  cat notes.md | slabprint print --size 30    print from stdin
  slabprint print --image photo.jpg --dither  print a picture
  slabprint schedule --at 07:30 -- print "Bins out"   print it later
  slabprint queue run                         run anything now due
  slabprint serve --token secret              print service for the LAN
  slabprint status                            is the printer reachable?

Add --preview out.png to any print to render without using paper.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys

from . import core, jobs, render, server, telegram


def entry_command() -> list[str]:
    """How to re-invoke this CLI, for queued jobs run by a timer."""
    installed = shutil.which("slabprint")
    return [installed] if installed else [sys.executable, "-m", "slabprint"]


def compose(args) -> list:
    """Build the bitmaps to print, from a PDF, an image, arguments or stdin.

    Returns a list because a PDF can contribute several pages, and each is a
    separate job: the printer cuts between them, so they arrive as separate
    notes rather than one long strip.
    """
    if args.image:
        source = args.image
        if source == "-":
            data = sys.stdin.buffer.read()
            if not data:
                raise ValueError("no data on standard input")
            source = io.BytesIO(data)
        if render.is_pdf(source):
            return render.load_pdf(
                source, pages=args.pages, dither=args.dither, overflow=args.overflow
            )
        return render.load_image(source, args.dither, overflow=args.overflow)
    lines = args.text or sys.stdin.read().splitlines()
    if not any(line.strip() for line in lines):
        raise SystemExit("nothing to print")
    return [render.render_text(lines, size=args.size, columns=args.columns, box=args.box)]


def emit(images, args) -> str:
    """Preview to a file, or pack and send each page to the printer."""
    if args.preview:
        # Several pages would overwrite each other, so number all but the first.
        results = []
        for number, image in enumerate(images, start=1):
            target = args.preview
            if number > 1:
                stem, _, extension = args.preview.rpartition(".")
                target = f"{stem}-{number}.{extension}" if stem else f"{args.preview}-{number}"
            image.save(target)
            results.append(f"{target} ({image.width}x{image.height})")
        return "preview written to " + ", ".join(results)

    sent = []
    for image in images:
        bitmap, width_bytes, height = render.pack(image)
        job = core.build_job(bitmap, width_bytes, height, args.copies, cut=not args.no_cut)
        if args.verbose:
            print(f"raster {width_bytes * 8}x{height}, job {len(job)} bytes")
        sent.append(core.send(job, args.transport, args.ble_address))
    return "; ".join(sent)


def print_for_bot(kind: str, payload) -> str:
    """Print on behalf of the Telegram bridge, reporting failures as text.

    The bridge must never crash on a bad message, so everything here is turned
    into a sentence the sender can read.
    """
    try:
        if kind == "status":
            faults = core.usb_status()
            if faults is None:
                return "printer not reachable — check it is on, or power-cycle it"
            return "printer ready" if not faults else "printer reports: " + ", ".join(faults)
        if kind == "text":
            return print_lines_for_server(str(payload).splitlines())
        if kind == "pdf":
            images = render.load_pdf(io.BytesIO(payload), pages="1", dither=True)
        else:
            images = render.load_image(io.BytesIO(payload), dither=True)
        for image in images:
            bitmap, width_bytes, height = render.pack(image)
            core.send(core.build_job(bitmap, width_bytes, height))
        return "printed"
    except Exception as exc:
        return f"could not print: {type(exc).__name__}: {exc}"


def print_lines_for_server(lines) -> str:
    bitmap, width_bytes, height = render.pack(render.render_text(lines, size=32))
    return core.send(core.build_job(bitmap, width_bytes, height))


def add_print_options(parser):
    parser.add_argument("--size", type=int, default=36, help="font size (default 36)")
    parser.add_argument("--box", action="store_true", help="draw a frame around the printed area")
    parser.add_argument(
        "--columns",
        type=int,
        default=1,
        metavar="N",
        help="lay the text out in N columns; saves paper on long lists",
    )
    parser.add_argument("--copies", type=int, default=1)
    parser.add_argument("--no-cut", action="store_true", help="leave the note uncut")
    parser.add_argument(
        "--transport",
        choices=["auto", "usb", "ble"],
        default="auto",
        help="auto tries USB first, then Bluetooth",
    )
    parser.add_argument("--ble-address", help="target a specific BLE device")
    parser.add_argument("--preview", metavar="PNG", help="render to a file instead of printing")
    parser.add_argument("-v", "--verbose", action="store_true")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="slabprint", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    printer = sub.add_parser("print", help="print text, stdin or an image")
    printer.add_argument("text", nargs="*", default=[])
    printer.add_argument(
        "--image",
        metavar="FILE",
        help='image or PDF file, or "-" to read one from stdin',
    )
    printer.add_argument(
        "--overflow",
        choices=render.OVERFLOW_MODES,
        default="scale",
        help="what to do when a page is taller than the printer allows: "
        "scale it down (default), split it across notes, or crop it",
    )
    printer.add_argument(
        "--pages",
        default="1",
        metavar="SPEC",
        help='PDF pages to print: "1", "2-5" or "all" (default: the first page)',
    )
    printer.add_argument(
        "--dither", action="store_true", help="dither photographs; the default threshold suits text"
    )
    printer.add_argument(
        "--self-test", action="store_true", help="the printer's built-in test page"
    )
    add_print_options(printer)

    schedule = sub.add_parser("schedule", help="queue a print for the future")
    schedule.add_argument("--at", required=True, help="'2026-09-15 07:30', '07:30', or '+90m'")
    schedule.add_argument("--label", help="a note to yourself, shown in queue list")
    schedule.add_argument(
        "rest", nargs=argparse.REMAINDER, help="the nemonic command to run, after --"
    )

    queue = sub.add_parser("queue", help="inspect or run the scheduled queue")
    queue.add_argument("action", nargs="?", default="list", choices=["list", "run", "clear"])

    serve = sub.add_parser("serve", help="HTTP print service for the local network")
    # Loopback by default: opening a print service to the whole network should
    # be a deliberate act, not what happens if you forget a flag.
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8719)
    serve.add_argument(
        "--token",
        default=os.environ.get("SLABPRINT_TOKEN"),
        help="shared secret required in the X-Token header; prefer the "
        "SLABPRINT_TOKEN environment variable, since a flag is visible in ps",
    )

    bot = sub.add_parser("telegram", help="print what is sent to a Telegram bot")
    bot.add_argument(
        "--whoami",
        action="store_true",
        help="list chat ids that have messaged the bot, to seed the allowlist",
    )

    sub.add_parser("status", help="report whether the printer is reachable")
    return parser


def subcommands() -> frozenset[str]:
    """The registered subcommand names, read from the parser itself.

    Derived rather than listed: a hardcoded copy silently rots the moment a
    command is added, and the symptom is the new command being printed as text
    instead of running.
    """
    for action in build_parser()._subparsers._group_actions:
        if action.choices:
            return frozenset(action.choices)
    return frozenset()


def with_default_command(argv: list[str]) -> list[str]:
    """Let printing be the default, since it is what the tool is mostly for.

        slabprint "Buy milk"        same as  slabprint print "Buy milk"
        pbpaste | slabprint         same as  slabprint print
        slabprint --image x.png     same as  slabprint print --image x.png

    A word that IS a subcommand still selects it, so `slabprint status` reports
    the printer rather than printing the word. Print that word with
    `slabprint print status`.
    """
    if argv and argv[0] in subcommands():
        return argv
    if argv and argv[0] in ("-h", "--help"):
        return argv
    if not argv and sys.stdin.isatty():
        return argv  # no arguments and nothing piped in: show help
    return ["print", *argv]


def main() -> int:
    args = build_parser().parse_args(with_default_command(sys.argv[1:]))

    if args.command == "print":
        if args.self_test:
            if args.preview:
                raise SystemExit(
                    "--self-test prints the printer's own built-in page, so there is "
                    "nothing to preview"
                )
            print(core.send(core.SELF_TEST, args.transport, args.ble_address))
        else:
            print(emit(compose(args), args))

    elif args.command == "schedule":
        rest = [token for token in args.rest if token != "--"]
        if not rest:
            raise SystemExit(
                "nothing to schedule, e.g. slabprint schedule --at 07:30 -- print 'Bins out'"
            )
        job = jobs.add(jobs.parse_when(args.at), rest, args.label)
        print(f"queued {job['id']} for {job['at']}: slabprint {' '.join(rest)}")

    elif args.command == "queue":
        if args.action == "list":
            queued = jobs.pending()
            for job in queued:
                label = f"   ({job['label']})" if job.get("label") else ""
                print(f"{job['id']}  {job['at']}  slabprint {' '.join(job['argv'])}{label}")
            print(f"{len(queued)} queued")
        elif args.action == "run":
            finished = jobs.run_due(entry_command())
            for job in finished:
                print(f"ran {job['id']} exit={job['exit']} {job.get('output', '')}")
            print(f"ran {len(finished)} due job(s)")
        else:
            print(f"cleared {jobs.clear()} job(s)")

    elif args.command == "telegram":
        token = telegram.resolve_token()
        if args.whoami:
            return telegram.whoami(token)
        bridge = telegram.Bridge(
            token=token,
            allowed=telegram.resolve_allowlist(),
            printer=print_for_bot,
            state_dir=jobs.STATE_DIR,
        )
        return bridge.run()

    elif args.command == "serve":
        server.serve(print_lines_for_server, args.host, args.port, args.token)

    elif args.command == "status":
        faults = core.usb_status()
        if faults is None:
            print(
                "printer not found on USB (is the cable a DATA cable, and the printer on?)",
                file=sys.stderr,
            )
            return 1
        print("printer ready" if not faults else "printer reports: " + ", ".join(faults))

    return 0


def run() -> int:
    """Console entry point: report failures as messages rather than tracebacks.

    Bad input reaches a surprising variety of libraries — Pillow raises its own
    errors for an unreadable image or an unknown preview extension, and the date
    parser raises ValueError with a message already written for a human. A
    traceback helps nobody holding a printer.
    """
    try:
        return main()
    except core.PrinterError as exc:
        print(f"print failed: {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"slabprint: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(run())
