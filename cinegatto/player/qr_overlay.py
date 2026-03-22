"""Generate QR code and ASCII art overlays for mpv."""

import logging
import tempfile

import qrcode
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("cinegatto.player.qr_overlay")

_BG_ALPHA = 160
_MARGIN = 16
_QR_SIZE = 200

_CAT_ART = r"""
  /\_/\
 ( o.o )
  > ^ <
 /|   |\
(_|   |_)
""".strip("\n")

_TITLE = "cinegatto"


def _find_mono_font(size):
    """Try to load a monospace font, fall back to default."""
    for name in ["Menlo.ttc", "DejaVuSansMono.ttf", "Courier New.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default(size=size)


def _rgba_to_bgra_file(img):
    """Convert an RGBA Pillow image to a raw BGRA temp file for mpv."""
    width, height = img.size
    pixels = img.load()
    raw = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            off = (y * width + x) * 4
            raw[off] = b
            raw[off + 1] = g
            raw[off + 2] = r
            raw[off + 3] = a
    tmp = tempfile.NamedTemporaryFile(suffix=".bgra", delete=False)
    tmp.write(raw)
    tmp.close()
    return tmp.name, width, height


def _generate_text_overlay(text, font_size=18, color=(255, 255, 255, 230)):
    """Render multi-line text to an RGBA image with semi-transparent background."""
    font = _find_mono_font(font_size)
    # Measure text
    tmp_img = Image.new("RGBA", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)
    bbox = tmp_draw.multiline_textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    # Create image with padding
    pad = _MARGIN
    img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, _BG_ALPHA))
    draw = ImageDraw.Draw(img)
    draw.multiline_text((pad, pad), text, font=font, fill=color)
    return img


def _generate_qr_with_url(url, size=_QR_SIZE):
    """Generate QR code image with URL text below it."""
    # QR code
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="white", back_color="black").convert("RGBA")
    qr_img = qr_img.resize((size, size), Image.NEAREST)

    # URL label
    font = _find_mono_font(14)
    tmp_img = Image.new("RGBA", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)
    bbox = tmp_draw.textbbox((0, 0), url, font=font)
    url_w = bbox[2] - bbox[0]
    url_h = bbox[3] - bbox[1]

    # Compose: QR + gap + URL text
    gap = 8
    total_w = max(size, url_w) + _MARGIN * 2
    total_h = size + gap + url_h + _MARGIN * 2

    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, _BG_ALPHA))
    # Center QR
    qr_x = (total_w - size) // 2
    img.paste(qr_img, (qr_x, _MARGIN))
    # Center URL text below
    draw = ImageDraw.Draw(img)
    url_x = (total_w - url_w) // 2
    draw.text((url_x, _MARGIN + size + gap), url, font=font, fill=(200, 200, 200, 220))

    return img


def apply_overlays(ipc, url, screen_width=1920, screen_height=1080):
    """Apply ASCII art (left) and QR code with URL (right) overlays."""
    # Left: cat ASCII art + title
    art_text = _CAT_ART + "\n\n " + _TITLE
    art_img = _generate_text_overlay(art_text, font_size=20)
    art_path, art_w, art_h = _rgba_to_bgra_file(art_img)

    # Right: QR code + URL
    qr_img = _generate_qr_with_url(url)
    qr_path, qr_w, qr_h = _rgba_to_bgra_file(qr_img)

    # Positions
    art_x, art_y = 20, 20
    qr_x = screen_width - qr_w - 20
    qr_y = 20

    try:
        ipc.command("overlay-add", 0, art_x, art_y, art_path, 0, "bgra", art_w, art_h, art_w * 4)
        logger.info("ASCII art overlay applied", extra={"x": art_x, "y": art_y})
    except Exception:
        logger.warning("Could not apply ASCII art overlay")

    try:
        ipc.command("overlay-add", 1, qr_x, qr_y, qr_path, 0, "bgra", qr_w, qr_h, qr_w * 4)
        logger.info("QR overlay applied", extra={"x": qr_x, "y": qr_y, "url": url})
    except Exception:
        logger.warning("Could not apply QR overlay")
