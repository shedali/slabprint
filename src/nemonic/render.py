"""Turn text and images into 1-bit bitmaps the printer understands."""

from __future__ import annotations

import textwrap
import warnings

from PIL import Image, ImageDraw, ImageFont

from .core import MAX_HEIGHT_PX, WIDTH_PX

# Fonts are looked up in order; the first that loads wins.
BOLD_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

# Roughly how wide an average glyph is relative to the font size. Used only to
# choose wrap points; the exact value just trades ragged edges against overflow.
GLYPH_RATIO = 0.55

# Nothing sensible is taller than the paper is wide. Beyond this, Pillow starts
# rasterising enormous glyphs and trips its own decompression-bomb guard, which
# is a confusing way to learn you typed an extra zero.
MAX_FONT_SIZE = 200


def check_size(size: int) -> None:
    if not 1 <= size <= MAX_FONT_SIZE:
        raise ValueError(f"size must be between 1 and {MAX_FONT_SIZE}, got {size}")


def load_font(size: int):
    for path in BOLD_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def wrap(text: str, size: int, usable_px: int) -> list[str]:
    """Wrap one line to `usable_px`, preserving its leading indent.

    Continuation lines get a hanging indent so wrapped items stay readable in a
    bulleted list.
    """
    check_size(size)
    if not text.strip():
        return [""]
    indent = len(text) - len(text.lstrip())
    columns = max(6, int(usable_px / (size * GLYPH_RATIO)) - indent)
    chunks = textwrap.wrap(text.strip(), width=columns) or [""]
    prefix = " " * indent
    return [prefix + chunks[0]] + [prefix + "  " + c for c in chunks[1:]]


def _warn_if_cropped(wanted: int, max_height: int) -> None:
    """Losing the end of a long document silently is worse than a noisy warning."""
    if wanted > max_height:
        warnings.warn(
            f"content is {wanted}px tall but the printer stops at {max_height}px; "
            "the end has been cropped. Use --columns or a smaller --size.",
            stacklevel=3,
        )


def _flatten(lines, size: int, usable_px: int) -> list[tuple]:
    """Expand source lines into drawable units: (kind, text, point_size).

    kind is "text", "rule" or "gap". Doing this before layout means a multi-column
    render can balance columns by what is actually drawn, not by source lines.
    """
    units = []
    for line in lines:
        if line.strip() in ("---", "___"):
            units.append(("rule", "", size))
            continue
        heading = line.startswith("# ")
        body = line[2:] if heading else line
        point = int(size * 1.35) if heading else size
        for piece in wrap(body, point, usable_px):
            if piece.strip():
                units.append(("text", piece, point))
            else:
                units.append(("gap", "", point))
        if heading:
            units.append(("gap", "", size // 3))
    return units


def _draw(draw, units, x: int, top: int, width_px: int, leading: int) -> int:
    """Draw units in a column starting at (x, top). Returns the y reached."""
    y = top
    for kind, text, point in units:
        if kind == "rule":
            y += 6
            draw.line([(x, y), (x + width_px, y)], fill=0, width=3)
            y += 12
        elif kind == "gap":
            y += point // 2
        else:
            draw.text((x, y), text, font=load_font(point), fill=0)
            y += point + leading
    return y


def render_text(
    lines,
    size: int = 36,
    margin: int = 18,
    leading: int = 12,
    columns: int = 1,
    gutter: int = 20,
    width: int = WIDTH_PX,
    max_height: int = MAX_HEIGHT_PX,
) -> Image.Image:
    """Render lines to a bitmap.

    Two pieces of markup are supported, chosen because they survive being typed
    into a shell argument:
      "# Heading"        renders larger
      "---" or "___"     draws a horizontal rule

    With columns > 1 the content is split across that many columns, balanced by
    drawn height, which suits long checklists and uses far less paper.
    """
    check_size(size)
    columns = max(1, columns)
    column_width = (width - 2 * margin - gutter * (columns - 1)) // columns
    units = _flatten(lines, size, column_width)

    if columns == 1:
        canvas = Image.new("1", (width, max_height), 1)
        height = _draw(ImageDraw.Draw(canvas), units, margin, margin, column_width, leading)
        _warn_if_cropped(height + margin, max_height)
        return canvas.crop((0, 0, width, min(max(height + margin, 40), max_height)))

    # Balance by cumulative drawn height rather than unit count, so a heading or
    # a rule does not push one column noticeably longer than the others.
    def unit_height(unit):
        kind, _text, point = unit
        return {"rule": 18, "gap": point // 2}.get(kind, point + leading)

    total = sum(unit_height(u) for u in units)
    target = total / columns

    chunks, current, used = [], [], 0
    for unit in units:
        if used >= target and len(chunks) < columns - 1:
            chunks.append(current)
            current, used = [], 0
        current.append(unit)
        used += unit_height(unit)
    chunks.append(current)

    canvas = Image.new("1", (width, max_height), 1)
    draw = ImageDraw.Draw(canvas)
    bottom = margin
    for index, chunk in enumerate(chunks):
        x = margin + index * (column_width + gutter)
        bottom = max(bottom, _draw(draw, chunk, x, margin, column_width, leading))

    _warn_if_cropped(bottom + margin, max_height)
    return canvas.crop((0, 0, width, min(max(bottom + margin, 40), max_height)))


def load_image(
    path: str,
    dither: bool = False,
    width: int = WIDTH_PX,
    max_height: int = MAX_HEIGHT_PX,
    mode: str = "1",
) -> Image.Image:
    """Load an image, flatten transparency, scale to `width`, reduce to `mode`.

    mode "1" is the printer's 1-bit format; pass "L" or "RGB" for displays that
    take greyscale or colour.
    """
    image = Image.open(path)
    if image.mode in ("RGBA", "LA", "P"):
        flattened = Image.new("RGB", image.size, "white")
        image = image.convert("RGBA")
        flattened.paste(image, mask=image.split()[-1])
        image = flattened
    image = image.convert("L")

    if image.width != width:
        height = max(1, round(image.height * width / image.width))
        image = image.resize((width, height), Image.LANCZOS)
    if image.height > max_height:
        image = image.crop((0, 0, width, max_height))

    if mode != "1":
        return image.convert(mode)
    return image.convert("1", dither=Image.FLOYDSTEINBERG if dither else Image.NONE)


def pack(image: Image.Image) -> tuple[bytes, int, int]:
    """Pack to the printer's raster format: 1 bit per pixel, MSB first, 1 = black.

    Returns (bitmap, width_in_bytes, height_in_rows).
    """
    if image.mode != "1":
        image = image.convert("1")
    width, height = image.size
    width_bytes = (width + 7) // 8
    pixels = image.load()

    out = bytearray()
    for y in range(height):
        for byte_index in range(width_bytes):
            byte = 0
            for bit in range(8):
                x = byte_index * 8 + bit
                if x < width and pixels[x, y] == 0:  # 0 is black in mode "1"
                    byte |= 0x80 >> bit
            out.append(byte)
    return bytes(out), width_bytes, height
