"""Generate QR code and ASCII art overlays for mpv.

Overlays show the web UI URL (as a QR code, top-right) and branding (cat art +
title, top-left). They're rendered once to BGRA files on disk and then applied
via mpv's overlay-add command.

Key design decisions:

  Repositioning on playback-restart: mpv's OSD dimensions (osd-width) are only
  accurate after a video is loaded and decoding starts. Before that, osd-width
  may be 0 or the previous video's dimensions. So we re-read osd-width and
  reposition overlays on every "playback-restart" event (fired when mpv begins
  rendering a new file or resumes after a seek).

  Threading requirement: The playback-restart callback runs on the IPC reader
  thread (see mpv_ipc.py). Repositioning requires IPC calls (get_property,
  overlay-add), which would deadlock if called from the reader thread (the
  reader can't deliver its own response while it's blocked calling command()).
  So _on_playback_restart spawns a short-lived thread to do the repositioning.
  This is cheap — it runs for ~10ms and exits.

  BGRA format: mpv's overlay-add expects raw pixel data in BGRA byte order
  (not RGBA). The _rgba_to_bgra_file helper does the channel swap and writes
  to a temp file that persists for the process lifetime.
"""

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
    """Convert an RGBA Pillow image to a raw BGRA temp file for mpv.

    Uses Pillow's C-level channel operations instead of a Python pixel loop.
    """
    r, g, b, a = img.split()
    bgra = Image.merge("RGBA", (b, g, r, a))
    raw = bgra.tobytes("raw", "RGBA")
    width, height = img.size
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
        # osd-width reflects the actual output resolution. Falls back to 1920
        # if unavailable (mpv in idle or on a weird display).
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
            logger.exception("Could not apply overlays")

    # Reposition when a video starts. The callback runs on the IPC reader thread,
    # but _position_overlays makes IPC calls (get_property, overlay-add).
    # Calling IPC from the reader thread would deadlock, so we spawn a thread.
    def _on_playback_restart(_event):
        t = threading.Thread(target=_position_overlays, daemon=True)
        t.start()

    ipc.on_event("playback-restart", _on_playback_restart)
    logger.info("Overlay images generated, will position on playback start")


def show_bootstrap_overlay(ipc, screen_width=1920, screen_height=1080):
    """Show a centered 'populating cache' message as overlay ID 2.

    Uses a rendered image so positioning is precise and it doesn't
    disappear like show-text does. Call hide_bootstrap_overlay() to remove.
    """
    text = "Populating cache\n(this may take a few minutes)"
    font = _find_mono_font(28)

    tmp = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(tmp)
    bbox = d.multiline_textbbox((0, 0), text, font=font, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    # Add a spinning-style unicode character
    spinner = "\u23F3"  # hourglass ⏳
    spinner_font = _find_mono_font(40)
    sbbox = d.textbbox((0, 0), spinner, font=spinner_font)
    sw = sbbox[2] - sbbox[0]
    sh = sbbox[3] - sbbox[1]

    pad = 30
    gap = 16
    total_w = max(tw, sw) + pad * 2
    total_h = sh + gap + th + pad * 2

    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 180))
    draw = ImageDraw.Draw(img)

    y = pad
    # Center hourglass
    draw.text(((total_w - sw) // 2, y), spinner, font=spinner_font, fill=(160, 160, 255, 230))
    y += sh + gap
    # Center text
    draw.multiline_text(((total_w - tw) // 2, y), text, font=font,
                        fill=(200, 200, 200, 230), align="center")

    path, w, h = _rgba_to_bgra_file(img)

    # Try to read actual screen size
    try:
        osd_w = ipc.get_property("osd-width") or screen_width
        osd_h = ipc.get_property("osd-height") or screen_height
    except Exception:
        osd_w, osd_h = screen_width, screen_height

    x = (osd_w - w) // 2
    y = (osd_h - h) // 2

    try:
        ipc.command("overlay-add", 2, x, y, path, 0, "bgra", w, h, w * 4)
        logger.info("Bootstrap overlay shown")
    except Exception:
        logger.debug("Could not show bootstrap overlay")


def hide_bootstrap_overlay(ipc):
    """Remove the bootstrap overlay (ID 2)."""
    try:
        ipc.command("overlay-remove", 2)
        logger.debug("Bootstrap overlay removed")
    except Exception:
        pass
