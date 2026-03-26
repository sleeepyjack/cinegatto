"""Pi 5 display power management via DRM/DPMS.

Pi 5 doesn't support vcgencmd display_power (that's Pi 4 and older).
Instead we write to /sys/class/drm/*/dpms to control HDMI power.
"""

import glob
import logging
import subprocess

logger = logging.getLogger("cinegatto.display.pi")


def _find_hdmi_dpms():
    """Find the DPMS sysfs path for the first connected HDMI output."""
    for path in sorted(glob.glob("/sys/class/drm/card*-HDMI-*/dpms")):
        # Check if connected
        status_path = path.replace("/dpms", "/status")
        try:
            with open(status_path) as f:
                if f.read().strip() == "connected":
                    return path
        except FileNotFoundError:
            continue
    # Fallback: try the first HDMI DPMS path regardless of status
    paths = sorted(glob.glob("/sys/class/drm/card*-HDMI-*/dpms"))
    return paths[0] if paths else None


class PiDisplay:
    """Controls HDMI display power on Raspberry Pi 5 via DRM DPMS."""

    def __init__(self):
        self._dpms_path = _find_hdmi_dpms()
        if self._dpms_path:
            logger.info("HDMI DPMS path: %s", self._dpms_path)
        else:
            logger.warning("No HDMI DPMS path found — display power control disabled")

    def power_on(self) -> None:
        logger.debug("Display power ON")
        self._set_dpms("On")

    def power_off(self) -> None:
        logger.debug("Display power OFF")
        self._set_dpms("Off")

    def _set_dpms(self, state: str) -> None:
        if not self._dpms_path:
            return
        try:
            # Needs root to write to sysfs — use tee via sudo
            subprocess.run(
                ["sudo", "tee", self._dpms_path],
                input=state.encode(), stdout=subprocess.DEVNULL, check=True,
            )
        except Exception:
            logger.exception("Failed to set DPMS to %s", state)
