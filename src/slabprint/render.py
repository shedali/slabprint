# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franz Sittampalam
"""Turn text and images into 1-bit bitmaps the printer understands."""

from __future__ import annotations

import io
import sys
import textwrap
import warnings
from pathlib import Path

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

# Frame geometry. The padding keeps text off the border; the line is thick enough
# to survive a 203 DPI thermal head without looking like a hairline.
BOX_PADDING = 14
BOX_LINE_WIDTH = 3


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


def _draw_frame(draw, width: int, height: int, margin: int) -> None:
    """Frame the printed area. Inset by a little so the border is never clipped."""
    draw.rectangle(
        [(margin // 2, margin // 2), (width - margin // 2 - 1, height - margin // 2 - 1)],
        outline=0,
        width=BOX_LINE_WIDTH,
    )


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
    box: bool = False,
) -> Image.Image:
    """Render lines to a bitmap.

    Two pieces of markup are supported, chosen because they survive being typed
    into a shell argument:
      "# Heading"        renders larger
      "---" or "___"     draws a horizontal rule

    With columns > 1 the content is split across that many columns, balanced by
    drawn height, which suits long checklists and uses far less paper.

    With box=True the content is framed. The frame is drawn last, once the height
    is known, so it wraps the content rather than a fixed-size area.
    """
    check_size(size)
    columns = max(1, columns)
    # The frame sits in the margin, so its content needs padding inside that.
    inset = margin + BOX_PADDING if box else margin
    column_width = (width - 2 * inset - gutter * (columns - 1)) // columns
    units = _flatten(lines, size, column_width)

    if columns == 1:
        canvas = Image.new("1", (width, max_height), 1)
        draw = ImageDraw.Draw(canvas)
        height = _draw(draw, units, inset, inset, column_width, leading)
        bottom = min(max(height + inset, 40), max_height)
        _warn_if_cropped(height + inset, max_height)
        if box:
            _draw_frame(draw, width, bottom, margin)
        return canvas.crop((0, 0, width, bottom))

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
    bottom = inset
    for index, chunk in enumerate(chunks):
        x = inset + index * (column_width + gutter)
        bottom = max(bottom, _draw(draw, chunk, x, inset, column_width, leading))

    _warn_if_cropped(bottom + inset, max_height)
    edge = min(max(bottom + inset, 40), max_height)
    if box:
        _draw_frame(draw, width, edge, margin)
    return canvas.crop((0, 0, width, edge))


PDF_MAGIC = b"%PDF"
# Rendering DPI for PDF pages. The printer is 203 DPI; rasterising a little
# above that and letting the downscale to 576px do the antialiasing gives
# noticeably cleaner text than rendering at the target size directly.
PDF_RENDER_SCALE = 3.0


def is_pdf(source) -> bool:
    """Sniff for a PDF, by content rather than by file extension."""
    if hasattr(source, "read"):
        position = source.tell()
        head = source.read(len(PDF_MAGIC))
        source.seek(position)
        return head == PDF_MAGIC
    try:
        with Path(source).open("rb") as handle:
            return handle.read(len(PDF_MAGIC)) == PDF_MAGIC
    except OSError:
        return False


def parse_pages(spec: str, total: int) -> list[int]:
    """Parse "1", "2-5" or "all" into zero-based page indices."""
    spec = (spec or "1").strip().lower()
    if spec == "all":
        return list(range(total))
    if "-" in spec:
        first, _, last = spec.partition("-")
        start, end = int(first), int(last)
    else:
        start = end = int(spec)
    if start < 1 or end < start:
        raise ValueError(f"cannot parse pages {spec!r}: try '1', '2-5' or 'all'")
    return list(range(start - 1, min(end, total)))


def load_pdf(source, pages: str = "1", dither: bool = False, **kwargs) -> list[Image.Image]:
    """Rasterise selected PDF pages, one printable bitmap each.

    Defaults to the first page only: a long document would otherwise quietly
    turn into a great many sticky notes.
    """
    try:
        import pypdfium2
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ValueError("printing a PDF needs pypdfium2 (pip install 'slabprint[pdf]')") from exc

    if source == "-":
        data = sys.stdin.buffer.read()
        if not data:
            raise ValueError("no PDF data on standard input")
        source = io.BytesIO(data)

    document = pypdfium2.PdfDocument(source)
    try:
        wanted = parse_pages(pages, len(document))
        if not wanted:
            raise ValueError(f"no such page in a {len(document)}-page document")
        rendered = []
        for index in wanted:
            page = document[index]
            bitmap = page.render(scale=PDF_RENDER_SCALE, grayscale=True)
            rendered.append(_fit(bitmap.to_pil(), dither=dither, **kwargs))
        return rendered
    finally:
        document.close()


def _fit(
    image: Image.Image,
    dither: bool,
    width: int = WIDTH_PX,
    max_height: int = MAX_HEIGHT_PX,
    mode: str = "1",
) -> Image.Image:
    """Scale to the paper width and reduce, shared by the image and PDF paths."""
    image = image.convert("L")
    if image.width != width:
        height = max(1, round(image.height * width / image.width))
        image = image.resize((width, height), Image.LANCZOS)
    if image.height > max_height:
        _warn_if_cropped(image.height, max_height)
        image = image.crop((0, 0, width, max_height))
    if mode != "1":
        return image.convert(mode)
    return image.convert("1", dither=Image.FLOYDSTEINBERG if dither else Image.NONE)


def load_image(
    source,
    dither: bool = False,
    width: int = WIDTH_PX,
    max_height: int = MAX_HEIGHT_PX,
    mode: str = "1",
) -> Image.Image:
    """Load an image, flatten transparency, scale to `width`, reduce to `mode`.

    `source` is a path, or "-" to read the image from standard input, which is
    what lets a clipboard tool or a download pipe straight into a print.

    mode "1" is the printer's 1-bit format; pass "L" or "RGB" for displays that
    take greyscale or colour.
    """
    if source == "-":
        # Buffer it: Pillow seeks while sniffing the format, and a pipe cannot.
        data = sys.stdin.buffer.read()
        if not data:
            raise ValueError("no image data on standard input")
        source = io.BytesIO(data)
    image = Image.open(source)
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
