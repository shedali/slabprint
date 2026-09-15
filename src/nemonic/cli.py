"""nemonic — print to a MangoSlab nemonic MIP-001 sticky-note printer.

  nemonic print "Buy milk"                  print a line of text
  cat notes.md | nemonic print --size 30    print from stdin
  nemonic print --image photo.jpg --dither  print a picture
  nemonic schedule --at 07:30 -- print "Bins out"   print it later
  nemonic queue run                         run anything now due
  nemonic serve --token secret              print service for the LAN
  nemonic status                            is the printer reachable?

Add --preview out.png to any print to render without using paper.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from . import core, jobs, render, server


def entry_command() -> list[str]:
    """How to re-invoke this CLI, for queued jobs run by a timer."""
    installed = shutil.which("nemonic")
    return [installed] if installed else [sys.executable, "-m", "nemonic"]


def compose(args) -> object:
    """Build the bitmap for a print command, from an image, arguments or stdin."""
    if args.image:
        return render.load_image(args.image, args.dither)
    lines = args.text or sys.stdin.read().splitlines()
    if not any(line.strip() for line in lines):
        raise SystemExit("nothing to print")
    return render.render_text(lines, size=args.size, columns=args.columns)


def emit(image, args) -> str:
    """Preview to a file, or pack and send to the printer."""
    if args.preview:
        image.save(args.preview)
        return f"preview written to {args.preview} ({image.width}x{image.height})"
    bitmap, width_bytes, height = render.pack(image)
    job = core.build_job(bitmap, width_bytes, height, args.copies, cut=not args.no_cut)
    if args.verbose:
        print(f"raster {width_bytes * 8}x{height}, job {len(job)} bytes")
    return core.send(job, args.transport, args.ble_address)


def print_lines_for_server(lines) -> str:
    bitmap, width_bytes, height = render.pack(render.render_text(lines, size=32))
    return core.send(core.build_job(bitmap, width_bytes, height))


def add_print_options(parser):
    parser.add_argument("--size", type=int, default=36, help="font size (default 36)")
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
        prog="nemonic", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    printer = sub.add_parser("print", help="print text, stdin or an image")
    printer.add_argument("text", nargs="*", default=[])
    printer.add_argument("--image", help="image file, scaled to 576px wide")
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
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8719)
    serve.add_argument(
        "--token",
        default=os.environ.get("NEMONIC_TOKEN"),
        help="shared secret required in the X-Token header",
    )

    sub.add_parser("status", help="report whether the printer is reachable")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "print":
        if args.self_test:
            print(core.send(core.SELF_TEST, args.transport, args.ble_address))
        else:
            print(emit(compose(args), args))

    elif args.command == "schedule":
        rest = [token for token in args.rest if token != "--"]
        if not rest:
            raise SystemExit(
                "nothing to schedule, e.g. nemonic schedule --at 07:30 -- print 'Bins out'"
            )
        job = jobs.add(jobs.parse_when(args.at), rest, args.label)
        print(f"queued {job['id']} for {job['at']}: nemonic {' '.join(rest)}")

    elif args.command == "queue":
        if args.action == "list":
            queued = jobs.pending()
            for job in queued:
                label = f"   ({job['label']})" if job.get("label") else ""
                print(f"{job['id']}  {job['at']}  nemonic {' '.join(job['argv'])}{label}")
            print(f"{len(queued)} queued")
        elif args.action == "run":
            finished = jobs.run_due(entry_command())
            for job in finished:
                print(f"ran {job['id']} exit={job['exit']} {job.get('output', '')}")
            print(f"ran {len(finished)} due job(s)")
        else:
            print(f"cleared {jobs.clear()} job(s)")

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
    """Console entry point: turn a printer fault into a clean error, not a traceback."""
    try:
        return main()
    except core.PrinterError as exc:
        print(f"print failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
