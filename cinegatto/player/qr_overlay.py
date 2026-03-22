"""Generate a QR code overlay for mpv's overlay-add command."""

import logging
import struct
import tempfile
from pathlib import Path

import qrcode
from PIL import Image

logger = logging.getLogger("cinegatto.player.qr_overlay")

# Semi-transparent dark background behind the QR code
_BG_ALPHA = 180
_MARGIN = 16
_QR_SIZE = 250


def generate_qr_overlay(url: str, size: int = _QR_SIZE) -> tuple[str, int, int]:
    """Generate a raw BGRA file for mpv's overlay-add command.

    Returns (file_path, width, height).
    The file is written to a temp location and persists for the process lifetime.
    """
    # Generate QR code image
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="white", back_color="black").convert("RGBA")

    # Scale to target size
    qr_img = qr_img.resize((size, size), Image.NEAREST)

    # Add padding with semi-transparent background
    total = size + _MARGIN * 2
    overlay = Image.new("RGBA", (total, total), (0, 0, 0, 0))

    # Draw rounded-ish dark background
    bg = Image.new("RGBA", (total, total), (0, 0, 0, _BG_ALPHA))
    overlay.paste(bg, (0, 0))

    # Paste QR code centered on background
    overlay.paste(qr_img, (_MARGIN, _MARGIN))

    # Convert to raw BGRA (mpv expects BGRA byte order)
    width, height = overlay.size
    pixels = overlay.load()
    raw_data = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            offset = (y * width + x) * 4
            raw_data[offset] = b      # B
            raw_data[offset + 1] = g  # G
            raw_data[offset + 2] = r  # R
            raw_data[offset + 3] = a  # A

    # Write to temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".bgra", delete=False)
    tmp.write(raw_data)
    tmp.close()

    logger.info("QR overlay generated", extra={
        "url": url, "size": f"{width}x{height}", "path": tmp.name,
    })
    return tmp.name, width, height


def apply_qr_overlay(ipc, url: str, x: int = 10, y: int = 10) -> None:
    """Generate a QR code and add it as an mpv overlay.

    Args:
        ipc: MpvIpc instance
        url: The URL to encode in the QR code
        x, y: Position of overlay (top-left corner)
    """
    path, w, h = generate_qr_overlay(url)
    stride = w * 4  # 4 bytes per pixel (BGRA)
    try:
        ipc.command("overlay-add", 0, x, y, path, 0, "bgra", w, h, stride)
        logger.info("QR overlay applied", extra={"x": x, "y": y, "w": w, "h": h})
    except Exception:
        logger.warning("Could not apply QR overlay (mpv may not support overlay-add)")
