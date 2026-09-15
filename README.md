# nemonic

Print to a **MangoSlab nemonic** sticky-note printer from the command line, on any
platform, with no vendor driver or app.

MangoSlab ships software for Windows, Android and iOS only, and their Nemonic Connect
desktop bridge is Windows-only. The printer itself is an ordinary USB printer-class
device speaking ESC/POS, so none of that is necessary. The protocol is documented in
[PROTOCOL.md](PROTOCOL.md).

Tested against a MIP-001 on macOS. The command set is shared across the MIP-101,
MIP-201 and MIP-301, so those should work; only the MIP-001 has been verified.

## Install

### Nix (flake)

```bash
nix run github:shedali/nemonic -- status        # run without installing
nix profile install github:shedali/nemonic      # install
```

Or add it to a flake:

```nix
inputs.nemonic.url = "github:shedali/nemonic";
# then: nemonic.packages.${system}.default
```

The flake wires libusb in for you. A dev shell with every dependency is available
with `nix develop`.

### Python

```bash
pipx install nemonic          # or: uv tool install nemonic
```

(Not published to PyPI yet — install from a checkout for now.)

Needs Python 3.10+, plus libusb for the USB transport. If it is installed somewhere
unusual, point `NEMONIC_LIBUSB` at the library file.

On Linux you may need a udev rule, or root, to claim the USB interface.

## Use

```bash
nemonic print "Buy milk"                        # a line of text
nemonic print "# Shopping" "milk" "bread"       # "# " makes a heading
cat notes.txt | nemonic print --size 30         # from stdin
nemonic print --image photo.jpg --dither        # a picture
nemonic print --self-test                       # the printer's own test page
nemonic print --columns 2 --size 20 < list.txt  # long checklist, half the paper
```

Add `--preview out.png` to any print to render it to a file instead of using paper.
Check the layout, then print.

### Scheduling

Queue a print for later — a paper reminder:

```bash
nemonic schedule --at 07:30          -- print "Bins out tonight"
nemonic schedule --at "+90m"         -- print "Take the bread out"
nemonic schedule --at "2026-12-25 08:00" --label xmas -- print "Happy Christmas"

nemonic queue list        # what is waiting
nemonic queue run         # print anything now due
nemonic queue clear
```

Jobs are single JSON files under `~/.local/state/nemonic/queue`, named by due time, so
you can inspect or delete them with ordinary file tools. Nothing prints until something
calls `nemonic queue run`, so put that on a timer — every minute is plenty:

**macOS** — save as `~/Library/LaunchAgents/local.nemonic.queue.plist`, then
`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/local.nemonic.queue.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.nemonic.queue</string>
  <key>ProgramArguments</key>
  <array>
    <string>/ABSOLUTE/PATH/TO/nemonic</string>
    <string>queue</string>
    <string>run</string>
  </array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
</dict></plist>
```

**Linux** — a one-line crontab entry:

```
* * * * * /ABSOLUTE/PATH/TO/nemonic queue run
```

### Printing from elsewhere on the network

```bash
export NEMONIC_TOKEN=secret            # not --token: a flag is visible in `ps`
nemonic serve --host 0.0.0.0
curl -X POST --data-binary @note.txt -H "X-Token: $NEMONIC_TOKEN" \
     http://printer-host:8719/print
```

It listens on `127.0.0.1` unless you ask otherwise, because opening a print service
to the whole network should be deliberate. There is no TLS and a header token is not
real authentication, so only do this on a network you trust.

## Options

| Option | Meaning |
|---|---|
| `--size N` | font size, default 36. 28–32 suits lists, 44+ suits headlines |
| `--columns N` | lay the text out in N columns — roughly halves the paper a long checklist uses |
| `--image PATH` | print an image, scaled to 576 px wide |
| `--dither` | Floyd–Steinberg, for photographs. The default threshold suits text |
| `--copies N` | number of copies |
| `--no-cut` | leave the note uncut |
| `--transport auto\|usb\|ble` | `auto` tries USB first, then Bluetooth |
| `--ble-address ADDR` | target a specific printer |
| `--preview PNG` | render to a file instead of printing |
| `-v` | show raster dimensions and job size |

Text markup: a line beginning `# ` is a heading; a line of `---` or `___` is a rule.
Long lines wrap automatically, preserving indentation.

## Layout

```
src/nemonic/
  core.py      protocol constants, job assembly, USB and BLE transports
  render.py    text and images to 1-bit bitmaps
  jobs.py      the scheduled-print queue
  server.py    the HTTP print service
  cli.py       the command line interface
```

`core.py` depends on nothing else in the package and works as a library:

```python
from nemonic import core, render

bitmap, width_bytes, height = render.pack(render.render_text(["Hello"]))
core.send(core.build_job(bitmap, width_bytes, height))
```

## Development

```bash
./scripts/install-hooks.sh  # install the commit gate — do this first
nix develop                 # shell with every dependency
pytest                      # the test suite
nix build                   # builds and runs the tests
```

The pre-commit hook runs `ruff check`, `ruff format --check` and the full test
suite, and every one of them is a hard failure. It fetches its tools with
[uvx](https://docs.astral.sh/uv/), so only `uv` needs to be installed; a missing
toolchain fails the commit rather than skipping the check, because a gate that
quietly does nothing is worse than no gate.

The tests cover protocol encoding, bitmap packing, text layout, the scheduled
queue and the HTTP service. They need no printer. Anything that does need one —
the USB and BLE transports — is deliberately left untested rather than mocked
into something that proves nothing.

## Troubleshooting

**"printer not found on USB"** — almost always a **charge-only USB cable**. The printer
powers up and never appears on the bus, silently. Try a known data cable first.

**"printer is not accepting data"**, or `nemonic status` saying it is not reachable
while the printer is plainly plugged in — the printer can wedge into a state where it
enumerates and answers control transfers but refuses all print data, on both USB and
Bluetooth. **Power-cycle it.** Also check the cartridge is seated and the cover closed;
`nemonic status` reports cover, paper, overheating and cutter faults.

**Nothing happens over Bluetooth** — make sure nothing else holds the printer, and
ignore any `/dev/cu.*` serial port it creates when paired. Classic SPP carries no print
data; see PROTOCOL.md.

## Licence

MIT.

## Not affiliated with MangoSlab

This is an independent, unofficial project. It is not affiliated with, endorsed by, or
supported by MangoSlab. *nemonic* and *MangoSlab* are the trademarks of their owner and
are used here only to say which printer this software talks to.

The protocol was determined by observing the vendor's own published application in order
to interoperate with hardware the author owns. No vendor code is reproduced here.
