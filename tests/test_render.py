"""Bitmap packing and text layout."""

from PIL import Image

from nemonic import core, render

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
    loaded = render.load_image(str(source), width=576)
    assert loaded.width == 576
    assert loaded.height == 288  # aspect ratio preserved


def test_load_image_crops_something_far_too_tall(tmp_path):
    source = tmp_path / "tall.png"
    Image.new("RGB", (100, 40_000), "white").save(source)
    assert render.load_image(str(source)).height == render.MAX_HEIGHT_PX


def test_load_image_flattens_transparency_instead_of_blackening_it(tmp_path):
    source = tmp_path / "clear.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(source)
    loaded = render.load_image(str(source))
    # A fully transparent image should come out white, not solid black.
    assert loaded.getpixel((0, 0)) == WHITE
