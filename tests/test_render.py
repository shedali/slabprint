# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franz Sittampalam
"""Bitmap packing and text layout."""

import io

import pytest
from PIL import Image, ImageDraw

from slabprint import core, render

# PIL mode "1" stores black as 0 and white as 255, even though it is 1 bit.
BLACK, WHITE = 0, 255


def one_bit(width, height, black_pixels=()):
    image = Image.new("1", (width, height), 1)  # 1 = white
    for x, y in black_pixels:
        image.putpixel((x, y), BLACK)
    return image


def test_pack_is_msb_first():
    # Leftmost pixel black => high bit set.
    data, width_bytes, height = render.pack(one_bit(8, 1, [(0, 0)]))
    assert (data, width_bytes, height) == (b"\x80", 1, 1)


def test_pack_sets_the_low_bit_for_the_rightmost_pixel():
    data, _, _ = render.pack(one_bit(8, 1, [(7, 0)]))
    assert data == b"\x01"


def test_pack_treats_white_as_unset():
    data, _, _ = render.pack(one_bit(8, 1))
    assert data == b"\x00"


def test_pack_rounds_width_up_to_whole_bytes():
    data, width_bytes, _ = render.pack(one_bit(9, 1, [(8, 0)]))
    assert width_bytes == 2
    # Pixel 8 is the FIRST bit of the second byte, and bits are MSB-first, so 0x80.
    assert data == b"\x00\x80"


def test_pack_emits_one_row_after_another():
    data, width_bytes, height = render.pack(one_bit(8, 2, [(0, 0), (7, 1)]))
    assert (width_bytes, height) == (1, 2)
    assert data == b"\x80\x01"


def test_pack_output_length_matches_the_raster_header():
    image = one_bit(576, 10)
    data, width_bytes, height = render.pack(image)
    assert len(data) == width_bytes * height
    assert core.raster_command(width_bytes, height)[4] == width_bytes


def test_wrap_keeps_a_short_line_whole():
    assert render.wrap("hello", 30, 500) == ["hello"]


def test_wrap_preserves_indentation_and_hangs_continuations():
    wrapped = render.wrap("  " + "word " * 40, 30, 300)
    assert len(wrapped) > 1
    assert wrapped[0].startswith("  ")
    # Continuation lines are indented further so list items stay readable.
    assert wrapped[1].startswith("    ")


def test_wrap_returns_a_single_empty_string_for_blank_input():
    assert render.wrap("   ", 30, 500) == [""]


def test_wrap_narrower_width_produces_more_lines():
    text = "word " * 30
    assert len(render.wrap(text, 30, 200)) > len(render.wrap(text, 30, 500))


def test_render_text_uses_the_printer_width_by_default():
    assert render.render_text(["hello"]).width == render.WIDTH_PX


def test_render_text_accepts_another_width():
    assert render.render_text(["hello"], width=480).width == 480


def test_render_text_grows_with_more_lines():
    short = render.render_text(["one"]).height
    tall = render.render_text(["one", "two", "three", "four"]).height
    assert tall > short


def test_render_text_never_exceeds_the_maximum_height():
    with pytest.warns(UserWarning):  # cropping is expected here, and warned about
        image = render.render_text(["line"] * 400, size=40)
    assert image.height <= render.MAX_HEIGHT_PX


def test_columns_are_shorter_than_one_column():
    lines = [f"item {n}" for n in range(40)]
    assert render.render_text(lines, columns=2).height < render.render_text(lines, columns=1).height


def test_a_rule_can_be_written_either_way():
    assert (
        render.render_text(["a", "---", "b"]).height == render.render_text(["a", "___", "b"]).height
    )


def test_a_heading_is_taller_than_plain_text():
    assert render.render_text(["# big"]).height > render.render_text(["big"]).height


def test_load_image_scales_to_the_target_width(tmp_path):
    source = tmp_path / "wide.png"
    Image.new("RGB", (1000, 500), "white").save(source)
    loaded = render.load_image(str(source), width=576)[0]
    assert loaded.width == 576
    assert loaded.height == 288  # aspect ratio preserved


def test_load_image_crops_something_far_too_tall(tmp_path):
    source = tmp_path / "tall.png"
    Image.new("RGB", (100, 40_000), "white").save(source)
    assert render.load_image(str(source), overflow="crop")[0].height == render.MAX_HEIGHT_PX


def test_load_image_flattens_transparency_instead_of_blackening_it(tmp_path):
    source = tmp_path / "clear.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(source)
    loaded = render.load_image(str(source))[0]
    # A fully transparent image should come out white, not solid black.
    assert loaded.getpixel((0, 0)) == WHITE


@pytest.mark.parametrize("size", [0, -1, render.MAX_FONT_SIZE + 1, 40_000])
def test_an_impossible_font_size_is_refused(size):
    with pytest.raises(ValueError):
        render.render_text(["hello"], size=size)


def test_cropping_long_content_warns_rather_than_losing_it_silently():
    with pytest.warns(UserWarning, match="cropped"):
        render.render_text(["line"] * 400, size=40)


def test_content_that_fits_does_not_warn(recwarn):
    render.render_text(["one line"], size=30)
    assert not [w for w in recwarn if "cropped" in str(w.message)]


def test_a_box_draws_a_border_on_all_four_sides():
    image = render.render_text(["framed"], size=30, box=True)
    inset = 18 // 2  # margin // 2, where _draw_frame puts the line
    mid_x, mid_y = image.width // 2, image.height // 2
    assert image.getpixel((mid_x, inset)) == BLACK, "no top border"
    assert image.getpixel((mid_x, image.height - inset - 1)) == BLACK, "no bottom border"
    assert image.getpixel((inset, mid_y)) == BLACK, "no left border"
    assert image.getpixel((image.width - inset - 1, mid_y)) == BLACK, "no right border"


def test_no_box_means_no_border():
    image = render.render_text(["plain"], size=30, box=False)
    # Assert "not black" rather than a literal white value: PIL reports white as
    # 1 for a freshly created mode "1" image but 255 for a converted one, and
    # what matters here is only that nothing was drawn.
    assert image.getpixel((image.width // 2, 18 // 2)) != BLACK


def test_a_box_leaves_room_for_the_frame():
    """Boxed content is padded inwards, so it is taller than the same text bare."""
    assert (
        render.render_text(["framed"], size=30, box=True).height
        > render.render_text(["framed"], size=30, box=False).height
    )


def test_a_box_works_with_columns():
    image = render.render_text([f"item {n}" for n in range(20)], size=24, columns=2, box=True)
    assert image.getpixel((image.width // 2, 18 // 2)) == BLACK


def test_an_image_can_be_read_from_stdin(tmp_path, monkeypatch):
    source = tmp_path / "in.png"
    Image.new("RGB", (120, 60), "white").save(source)

    class FakeStdin:
        buffer = io.BytesIO(source.read_bytes())

    monkeypatch.setattr(render.sys, "stdin", FakeStdin)
    assert render.load_image("-")[0].width == render.WIDTH_PX


def test_empty_stdin_is_a_clean_error(monkeypatch):
    class FakeStdin:
        buffer = io.BytesIO(b"")

    monkeypatch.setattr(render.sys, "stdin", FakeStdin)
    with pytest.raises(ValueError, match="no image data"):
        render.load_image("-")


def make_pdf(path, pages=2):
    images = [Image.new("RGB", (620, 877), "white") for _ in range(pages)]
    for number, image in enumerate(images, start=1):
        ImageDraw.Draw(image).text((40, 40), f"page {number}", fill="black")
    images[0].save(path, save_all=True, append_images=images[1:])
    return path


def test_a_pdf_is_detected_by_content(tmp_path):
    assert render.is_pdf(str(make_pdf(tmp_path / "doc.pdf")))


def test_a_png_is_not_mistaken_for_a_pdf(tmp_path):
    source = tmp_path / "x.png"
    Image.new("RGB", (10, 10), "white").save(source)
    assert not render.is_pdf(str(source))


def test_a_missing_file_is_not_a_pdf(tmp_path):
    assert not render.is_pdf(str(tmp_path / "nope.pdf"))


def test_loading_a_pdf_defaults_to_the_first_page_only(tmp_path):
    pages = render.load_pdf(str(make_pdf(tmp_path / "doc.pdf", pages=3)))
    assert len(pages) == 1
    assert pages[0].width == render.WIDTH_PX


def test_all_pages_can_be_requested(tmp_path):
    assert len(render.load_pdf(str(make_pdf(tmp_path / "doc.pdf", pages=3)), pages="all")) == 3


def test_a_page_range_can_be_requested(tmp_path):
    assert len(render.load_pdf(str(make_pdf(tmp_path / "doc.pdf", pages=4)), pages="2-3")) == 2


@pytest.mark.parametrize(
    "spec, total, expected",
    [
        ("1", 5, [0]),
        ("2-4", 5, [1, 2, 3]),
        ("all", 3, [0, 1, 2]),
        ("2-99", 3, [1, 2]),
    ],
)
def test_page_specs_parse(spec, total, expected):
    assert render.parse_pages(spec, total) == expected


@pytest.mark.parametrize("spec", ["0", "3-1", "-1", "two"])
def test_nonsense_page_specs_are_refused(spec):
    with pytest.raises(ValueError):
        render.parse_pages(spec, 5)


def tall_image(tmp_path, ratio=12):
    source = tmp_path / "tall.png"
    image = Image.new("RGB", (400, 400 * ratio), "white")
    ImageDraw.Draw(image).text((20, 400 * ratio - 60), "BOTTOM", fill="black")
    image.save(source)
    return str(source)


def test_scale_keeps_an_over_tall_page_in_one_note(tmp_path):
    pages = render.load_image(tall_image(tmp_path), overflow="scale")
    assert len(pages) == 1
    assert pages[0].height <= render.MAX_HEIGHT_PX


def test_split_continues_onto_further_notes(tmp_path):
    pages = render.load_image(tall_image(tmp_path), overflow="split")
    assert len(pages) > 1
    assert all(page.height <= render.MAX_HEIGHT_PX for page in pages)


def test_crop_keeps_only_the_first_note(tmp_path):
    with pytest.warns(UserWarning):
        pages = render.load_image(tall_image(tmp_path), overflow="crop")
    assert len(pages) == 1


def test_an_unknown_overflow_mode_is_refused(tmp_path):
    with pytest.raises(ValueError, match="overflow"):
        render.load_image(tall_image(tmp_path), overflow="shred")


def test_a_page_that_already_fits_is_untouched_by_every_mode(tmp_path):
    source = tmp_path / "fits.png"
    Image.new("RGB", (600, 400), "white").save(source)
    for mode in render.OVERFLOW_MODES:
        pages = render.load_image(str(source), overflow=mode)
        assert len(pages) == 1
