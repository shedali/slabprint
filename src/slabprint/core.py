"""Protocol and transports for the nemonic (MangoSlab) MIP-001 sticky-note printer.

The printer is a standard USB printer-class device that also exposes a BLE GATT
service. It speaks ESC/POS. No vendor driver is required on any platform.

See PROTOCOL.md for how this command set was derived and what every byte means.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes.util
import os
import subprocess
from pathlib import Path

# Hardware limits, from the vendor app's Mip001HardwareSpec.
WIDTH_PX = 576
MAX_HEIGHT_PX = 2176
DPI = 203

# USB identity.
USB_VID = 0x1C8A
USB_PID = 0x3A21
USB_PRINTER_CLASS = 7

# BLE GATT profile for MIP-001 and MIP-101.
BLE_SERVICE = "00005000-d102-11e1-9b23-74f07d000000"
BLE_WRITE_CHAR = "00005001-d102-11e1-9b23-74f07d000000"
BLE_NOTIFY_CHAR = "00005002-d102-11e1-9b23-74f07d000000"

# ESC/POS commands.
INIT = bytes([0x1B, 0x40])  # ESC @
EXECUTE_PRINT = bytes([0x1B, 0x50])  # print and cut
EXECUTE_PRINT_NO_CUT = bytes([0x1B, 0x51])
EXECUTE_CUT = bytes([0x1B, 0x69])
SELF_TEST = bytes([0x1C, 0x7A, 0x53, 0x50])
STATUS_QUERY = bytes([0x10, 0x04, 0x01])

# Bits the printer sets in every status reply; they mean nothing on their own.
ALWAYS_SET_BITS = 0x12

# Status bits, after clearing ALWAYS_SET_BITS.
STATUS_BITS = {0x04: "cover open", 0x08: "overheated", 0x20: "out of paper", 0x40: "cutter jammed"}


class PrinterError(RuntimeError):
    """Raised when the printer is present but will not accept data."""


def set_copies(n: int) -> bytes:
    """ESC C n — number of copies."""
    return bytes([0x1B, 0x43, max(1, min(255, n))])


def raster_command(width_bytes: int, height: int) -> bytes:
    """GS v 0 — raster bit image header. The MIP-001 magic number is 0.

    Both dimensions are 16 bit on the wire. Silently truncating would produce a
    job the printer accepts and renders as garbage, so refuse instead.
    """
    for name, value in (("width_bytes", width_bytes), ("height", height)):
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"{name} must fit in 16 bits, got {value}")
    return bytes(
        [
            0x1D,
            0x76,
            0x30,
            0x00,
            width_bytes & 0xFF,
            (width_bytes >> 8) & 0xFF,
            height & 0xFF,
            (height >> 8) & 0xFF,
        ]
    )


def decode_status(byte_: int) -> list[str]:
    """Decode a status response into human-readable faults. Empty means ready.

    Bits 0x02 and 0x10 are set in every reply and carry no fault meaning, so they
    are cleared first. None of the bits in STATUS_BITS overlaps them, so today
    this changes no result; it is kept so that adding a bit later cannot quietly
    start reporting a fault on every healthy printer.
    """
    masked = byte_ & ~ALWAYS_SET_BITS
    return [name for bit, name in STATUS_BITS.items() if masked & bit]


def build_job(
    bitmap: bytes, width_bytes: int, height: int, copies: int = 1, cut: bool = True
) -> bytes:
    """Assemble a complete print job from a packed 1-bit bitmap."""
    return (
        INIT
        + set_copies(copies)
        + raster_command(width_bytes, height)
        + bitmap
        + (EXECUTE_PRINT if cut else EXECUTE_PRINT_NO_CUT)
    )


# --------------------------------------------------------------------------
# USB transport
# --------------------------------------------------------------------------


def _find_libusb() -> str | None:
    """Locate libusb, preferring an explicit override, then the usual places."""
    if os.environ.get("SLABPRINT_LIBUSB"):
        return os.environ["SLABPRINT_LIBUSB"]
    if found := ctypes.util.find_library("usb-1.0"):
        return found
    for candidate in (
        "/opt/homebrew/lib/libusb-1.0.dylib",
        "/usr/local/lib/libusb-1.0.dylib",
        "/usr/lib/x86_64-linux-gnu/libusb-1.0.so.0",
    ):
        if Path(candidate).exists():
            return candidate
    # Only real shared objects: a sibling .la libtool archive would match a
    # looser glob and then fail to load with a confusing "no backend" error.
    for pattern in ("*libusb*/lib/libusb-1.0.dylib", "*libusb*/lib/libusb-1.0.so.0"):
        if hits := sorted(Path("/nix/store").glob(pattern)):
            return str(hits[-1])
    try:  # last resort on a Nix machine
        out = subprocess.run(
            ["nix", "build", "--no-link", "--print-out-paths", "nixpkgs#libusb1"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        path = Path(out.stdout.strip().splitlines()[-1]) / "lib/libusb-1.0.dylib"
        return str(path) if path.exists() else None
    except Exception:
        return None


def usb_open():
    """Return (device, ep_out, ep_in), or None if the printer is not on USB."""
    import usb.backend.libusb1
    import usb.core
    import usb.util

    lib = _find_libusb()
    backend = usb.backend.libusb1.get_backend(find_library=lambda _: lib) if lib else None
    device = usb.core.find(idVendor=USB_VID, idProduct=USB_PID, backend=backend)
    if device is None:
        return None

    interface = next(
        (i for i in device.get_active_configuration() if i.bInterfaceClass == USB_PRINTER_CLASS),
        None,
    )
    if interface is None:
        return None
    # Already claimed by this process is fine; anything else surfaces on transfer.
    with contextlib.suppress(Exception):
        usb.util.claim_interface(device, interface.bInterfaceNumber)

    def endpoint(direction):
        return usb.util.find_descriptor(
            interface,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == direction,
        )

    return device, endpoint(usb.util.ENDPOINT_OUT), endpoint(usb.util.ENDPOINT_IN)


def usb_send(job: bytes, chunk: int = 4096) -> str:
    """Send a job over USB. Raises PrinterError if the printer refuses data."""
    opened = usb_open()
    if opened is None:
        raise PrinterError("MIP-001 not found on USB")
    _device, ep_out, _ep_in = opened

    # A short probe write first: the printer can enumerate and answer control
    # transfers while refusing all bulk data. Failing here gives a useful message
    # instead of a confusing timeout part-way through a large raster.
    try:
        ep_out.write(INIT, timeout=4000)
    except Exception as exc:
        raise PrinterError(
            f"printer is not accepting data ({exc}). Power-cycle it, and check the "
            "cartridge is seated and the cover closed."
        ) from exc

    sent = sum(ep_out.write(job[i : i + chunk], timeout=15000) for i in range(0, len(job), chunk))
    return f"sent {sent} bytes over USB"


def usb_status() -> list[str] | None:
    """Query printer faults over USB. None if unreachable, [] if ready.

    A printer that enumerates but will not answer is NOT ready — that is exactly
    the wedged state a power cycle fixes — so a failed exchange reports
    unreachable rather than a clean bill of health.
    """
    opened = usb_open()
    if opened is None:
        return None
    _device, ep_out, ep_in = opened
    try:
        ep_out.write(STATUS_QUERY, timeout=4000)
        reply = bytes(ep_in.read(64, timeout=2500))
    except Exception:
        return None
    return decode_status(reply[0]) if reply else None


# --------------------------------------------------------------------------
# Bluetooth LE transport
# --------------------------------------------------------------------------


async def _ble_send(job: bytes, address: str | None, chunk: int, settle: float) -> str:
    from bleak import BleakClient, BleakScanner

    device = None
    if address:
        device = await BleakScanner.find_device_by_address(address, timeout=15.0)
        if device is None:
            # An explicit address is a request for THAT printer. Falling back to
            # whatever else advertises the service could print on a stranger's.
            raise PrinterError(f"no printer found at {address}")
    if device is None:
        for _, (candidate, advert) in (
            await BleakScanner.discover(timeout=15.0, return_adv=True)
        ).items():
            name = (candidate.name or advert.local_name or "").lower()
            if BLE_SERVICE in [u.lower() for u in advert.service_uuids] or "nemonic" in name:
                device = candidate
                break
    if device is None:
        raise PrinterError("no nemonic printer found over BLE")

    async with BleakClient(device, timeout=25.0) as client:
        for i in range(0, len(job), chunk):
            await client.write_gatt_char(BLE_WRITE_CHAR, job[i : i + chunk], response=False)
            await asyncio.sleep(0.02)
        await asyncio.sleep(settle)  # hold the link while the printer works
    return f"sent {len(job)} bytes over BLE"


def ble_send(job: bytes, address: str | None = None, chunk: int = 180, settle: float = 6.0) -> str:
    return asyncio.run(_ble_send(job, address, chunk, settle))


def send(job: bytes, transport: str = "auto", ble_address: str | None = None) -> str:
    """Send a job. 'auto' tries USB first (much faster), then BLE."""
    attempts = {"auto": ("usb", "ble"), "usb": ("usb",), "ble": ("ble",)}[transport]
    failures = []
    for name in attempts:
        try:
            return usb_send(job) if name == "usb" else ble_send(job, ble_address)
        except PrinterError as exc:
            failures.append(f"{name}: {exc}")
        except Exception as exc:  # missing optional dependency, adapter off, ...
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    raise PrinterError("; ".join(failures))
