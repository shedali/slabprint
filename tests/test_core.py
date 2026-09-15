"""Protocol encoding. These are the bytes that go down the wire, so they are
asserted literally rather than via the helpers that produce them."""

import pytest

from nemonic import core


def test_set_copies_is_esc_c():
    assert core.set_copies(1) == bytes([0x1B, 0x43, 0x01])
    assert core.set_copies(7) == bytes([0x1B, 0x43, 0x07])


@pytest.mark.parametrize("requested, encoded", [(0, 1), (-5, 1), (300, 255), (255, 255)])
def test_set_copies_clamps_to_one_byte(requested, encoded):
    assert core.set_copies(requested)[2] == encoded


def test_raster_command_encodes_little_endian_16_bit():
    # 72 bytes wide (576px) by 800 rows.
    assert core.raster_command(72, 800) == bytes([0x1D, 0x76, 0x30, 0x00, 72, 0, 0x20, 0x03])


def test_raster_command_splits_values_above_255():
    command = core.raster_command(300, 2176)
    assert command[4:6] == bytes([300 & 0xFF, 300 >> 8])
    assert command[6:8] == bytes([2176 & 0xFF, 2176 >> 8])


def test_decode_status_ready_is_empty():
    assert core.decode_status(0x00) == []


def test_decode_status_ignores_the_always_set_bits():
    # Bits 0x12 are set in every reply and must not read as a fault.
    assert core.decode_status(0x12) == []


@pytest.mark.parametrize(
    "bit, fault",
    [
        (0x04, "cover open"),
        (0x08, "overheated"),
        (0x20, "out of paper"),
        (0x40, "cutter jammed"),
    ],
)
def test_decode_status_reports_each_fault(bit, fault):
    assert core.decode_status(bit | 0x12) == [fault]


def test_decode_status_reports_several_faults_at_once():
    assert set(core.decode_status(0x04 | 0x20)) == {"cover open", "out of paper"}


def test_build_job_frames_the_bitmap():
    bitmap = b"\xff" * 4
    job = core.build_job(bitmap, width_bytes=1, height=4)
    assert job.startswith(core.INIT + core.set_copies(1) + core.raster_command(1, 4))
    assert bitmap in job
    assert job.endswith(core.EXECUTE_PRINT)


def test_build_job_can_skip_the_cut():
    job = core.build_job(b"\x00", 1, 1, cut=False)
    assert job.endswith(core.EXECUTE_PRINT_NO_CUT)
    assert not job.endswith(core.EXECUTE_PRINT)


def test_send_rejects_an_unknown_transport():
    with pytest.raises(KeyError):
        core.send(b"", transport="carrier-pigeon")
