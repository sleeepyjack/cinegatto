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
""".strip("\n")

_TITLE = "cinegatto"
_GITHUB_URL = "github.com/sleeepyjack/cinegatto"


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


def _generate_text_overlay(text, font_size=18, color=(255, 255, 255, 230),
                           target_height=None):
    """Render multi-line text to an RGBA image with semi-transparent background.

    If target_height is given, scales the font to roughly match that height.
    """
    if target_height:
        # Binary search for font size that fills the target height
        for fs in range(30, 8, -1):
            font = _find_mono_font(fs)
            tmp_img = Image.new("RGBA", (1, 1))
            tmp_draw = ImageDraw.Draw(tmp_img)
            bbox = tmp_draw.multiline_textbbox((0, 0), text, font=font)
            th = bbox[3] - bbox[1] + _MARGIN * 2
            if th <= target_height:
                font_size = fs
                break

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


def _generate_art_overlay():
    """Generate the left-side overlay: cat art + title + github URL."""
    font_art = _find_mono_font(22)
    font_title = _find_mono_font(18)
    font_url = _find_mono_font(12)

    tmp = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(tmp)

    # Measure each piece
    art_bbox = d.multiline_textbbox((0, 0), _CAT_ART, font=font_art)
    art_w, art_h = art_bbox[2] - art_bbox[0], art_bbox[3] - art_bbox[1]

    title_bbox = d.textbbox((0, 0), _TITLE, font=font_title)
    title_w, title_h = title_bbox[2] - title_bbox[0], title_bbox[3] - title_bbox[1]

    url_bbox = d.textbbox((0, 0), _GITHUB_URL, font=font_url)
    url_w, url_h = url_bbox[2] - url_bbox[0], url_bbox[3] - url_bbox[1]

    # Layout
    pad = _MARGIN
    gap = 8
    content_w = max(art_w, title_w, url_w)
    content_h = art_h + gap + title_h + gap + url_h
    total_w = content_w + pad * 2
    total_h = content_h + pad * 2

    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, _BG_ALPHA))
    draw = ImageDraw.Draw(img)

    y = pad
    # Center cat art
    draw.multiline_text(((total_w - art_w) // 2, y), _CAT_ART,
                        font=font_art, fill=(255, 255, 255, 230))
    y += art_h + gap
    # Center title
    draw.text(((total_w - title_w) // 2, y), _TITLE,
              font=font_title, fill=(255, 255, 255, 230))
    y += title_h + gap
    # Center URL (dimmer)
    draw.text(((total_w - url_w) // 2, y), _GITHUB_URL,
              font=font_url, fill=(180, 180, 180, 200))

    return img


def apply_overlays(ipc, url):
    """Apply ASCII art (left) and QR code (right) overlays.

    Images are generated once. Position is recalculated each time a video
    starts playing (playback-restart event), when osd-width is accurate.
    """
    import threading

    # Generate images once (expensive)
    art_img = _generate_art_overlay()
    art_path, art_w, art_h = _rgba_to_bgra_file(art_img)

    qr_img = _generate_qr_with_url(url)
    qr_path, qr_w, qr_h = _rgba_to_bgra_file(qr_img)

    def _position_overlays():
        try:
            osd_w = ipc.get_property("osd-width") or 1920
        except Exception:
            osd_w = 1920

        art_x, art_y = 20, 20
        qr_x = max(osd_w - qr_w - 20, 0)
        qr_y = 20

        try:
            ipc.command("overlay-add", 0, art_x, art_y, art_path, 0, "bgra", art_w, art_h, art_w * 4)
            ipc.command("overlay-add", 1, qr_x, qr_y, qr_path, 0, "bgra", qr_w, qr_h, qr_w * 4)
            logger.debug("Overlays positioned", extra={"osd_w": osd_w, "qr_x": qr_x})
        except Exception:
            logger.warning("Could not apply overlays")

    # Reposition when a video starts (callback runs on reader thread — defer IPC calls)
    def _on_playback_restart(_event):
        t = threading.Thread(target=_position_overlays, daemon=True)
        t.start()

    ipc.on_event("playback-restart", _on_playback_restart)
    logger.info("Overlay images generated, will position on playback start")
