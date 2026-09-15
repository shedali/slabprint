# nemonic MIP-001 printer protocol

Everything needed to drive a MangoSlab **nemonic** sticky-note printer without the
vendor's software.

> Independent documentation, not affiliated with or endorsed by MangoSlab Co., Ltd.
> It records facts about a wire protocol — byte values, identifiers and layouts —
> established in order to interoperate with hardware the author owns. It reproduces
> no vendor code, assets or documentation. See [NOTICE](NOTICE). MangoSlab publishes no protocol documentation and ships no macOS
driver, so this was recovered from their own Windows application.

## Summary

The printer is a **standard USB printer-class device that speaks ESC/POS**. It also
exposes a **BLE GATT** service carrying the identical byte stream. Nothing about it
requires a vendor driver on any platform.

It reports itself over USB as:

```
MFG:nemonic ;CMD:ESC,STAR;MDL:MIP-001;CLS:PRINTER;
```

## Hardware

| | MIP-001 |
|---|---|
| Resolution | 203 DPI |
| Printable width | **576 px** (72 bytes) |
| Maximum height | 2176 px |
| Colour | black only (direct thermal) |
| Battery | none |

Other models exist — MIP-101, MIP-201 ("AI"), MIP-301 — sharing the command set but
differing in BLE profile and width.

## Transports

### USB (preferred — much faster)

| | |
|---|---|
| Vendor ID | `0x1C8A` |
| Product ID | `0x3A21` |
| Interface | class 7 (printer), subclass 1, protocol 2 (bidirectional) |
| Endpoints | `0x02` bulk OUT, `0x81` bulk IN, 64-byte packets |

Write the job to the bulk OUT endpoint. One configuration, one interface, no
alternate settings.

### Bluetooth LE

| Model | Service | Write characteristic | Notify characteristic |
|---|---|---|---|
| MIP-001, MIP-101 | `00005000-d102-11e1-9b23-74f07d000000` | `…5001` | `…5002` |
| MIP-201 | `3b790000-923e-4f69-b794-74f07d000000` | `3b790002-…` | `3b790001-…` |
| MIP-301 | `49535343-FE78-4AE5-8FA9-9FAFD205E455` | `49535343-8841-…` | `49535343-1E4D-…` |

The write characteristic is **write-without-response**. Send in chunks (180 bytes
works well) with a small delay between them, and hold the connection open for several
seconds afterwards while the printer works. MIP-201 additionally needs about one
second of settling time after connecting before you send image data.

### Bluetooth Classic SPP — a dead end

The printer also pairs as a Classic SPP device, and macOS will happily create a
`/dev/cu.<name>` serial port for it. **That port carries no print data.** The RFCOMM
channel never establishes, and the printer drops the ACL link when written to. Ignore it.

## Command set

All values hexadecimal.

| Command | Bytes | Notes |
|---|---|---|
| Initialise | `1B 40` | ESC @ |
| Set copies | `1B 43 n` | ESC C |
| Raster image | `1D 76 30 00 wl wh hl hh …` | GS v 0, see below |
| Print and cut | `1B 50` | |
| Print, no cut | `1B 51` | |
| Cut | `1B 69` | |
| Self test | `1C 7A 53 50` | prints a test page, needs no host data |
| Status query | `10 04 01` | DLE EOT 1 |
| Printer status | `10 04 00` | |
| Feed pixels | `1B 46 lo hi` | |
| Feed lines | `1B 64 n 00` | |
| Label cut mode | `1B 6C 31` / `1B 6C 30` | on / off |
| Print speed | `1C 65 n` | |
| Read cartridge | `1C 70` | |
| Device information | `1D 49` | |
| Begin USB comms | `02` | |
| End USB comms | `03` | |

### Raster image

```
1D 76 30 00  <width_bytes LE16>  <height LE16>  <bitmap>
```

The bitmap is 1 bit per pixel, **MSB first**, `1` = black, packed into
`width_bytes` per row with no padding between rows. `width_bytes` is simply
`width / 8`; the vendor code carries a per-model "magic number" added before the
division, but for MIP-001 it is zero.

### A complete job

```
1B 40                                    initialise
1B 43 01                                 one copy
1D 76 30 00 48 00 DC 00  <bitmap>        576x220 raster
1B 50                                    print and cut
```

### Status byte

Mask the response with `~0x12`, then:

| Bit | Meaning |
|---|---|
| `0x04` | cover open |
| `0x08` | overheated |
| `0x20` | out of paper |
| `0x40` | cutter jammed |

Zero means ready.

## Traps

**Charge-only USB cables.** The printer powers up and simply never enumerates. There
is no error anywhere — the device is just absent from the bus. Suspect the cable before
anything else.

**A wedged printer accepts no data on either transport.** It can enumerate, answer USB
control transfers and return its device ID while refusing every bulk write, and
simultaneously accept a whole BLE job without printing. A **power cycle** clears it.
If writes time out, do that before debugging anything else.

**macOS Bluetooth serial ports lie.** `open()` on `/dev/cu.<device>` succeeds instantly
and `write()` reports success even when no RFCOMM link exists; the bytes sit in the TTY
buffer forever with no error. To test a link, open non-blocking and push more than the
buffer: a dead link stalls at exactly 1024 bytes and then returns `EAGAIN` forever, while
a live one drains 32 KB almost instantly. A *blocking* write to a dead port wedges the
process past even `SIGALRM`, so always probe with `O_NONBLOCK`.

**macOS no longer supports CUPS raw queues**, so `lpadmin -m raw` cannot be used to make
a print queue. Talk to the USB endpoint directly instead.

## How this was determined

Recorded so the findings can be checked, not as an invitation to repeat it. The vendor's
Windows desktop application was examined in order to interoperate with hardware the
author owns. No vendor code is reproduced in this repository.

1. The vendor's published installer manifest names an MSIX bundle.
2. That bundle unpacks (it is an ordinary zip, twice over) to a .NET MAUI application
   whose logic lives in `Mango.dll`.
3. Decompiling with `ilspycmd` shows the relevant types: `SharedCommands`,
   `Mip001CommandProtocol`, `BleProfileRegistry` and `Mip001HardwareSpec`. An XML
   documentation file ships alongside describing the API surface.
4. **Static `byte[]` constants do not survive decompilation** — ILSpy emits
   `RuntimeHelpers.InitializeArray(... LdMemberToken)` instead of values, and the assembly
   is a Windows ReadyToRun image so it will not load on macOS to be reflected over. Read
   them out of the PE metadata: parse the `.cctor` IL, follow each `ldtoken` to its
   `<PrivateImplementationDetails>` field, and resolve the FieldRVA with
   `System.Reflection.Metadata` plus `PEReader.GetSectionData`. Arrays of one or two bytes
   are built inline with `stelem.i1` rather than `InitializeArray`, so both shapes matter.

The command values above are facts about a wire protocol, which is what makes an
independent implementation possible at all.
