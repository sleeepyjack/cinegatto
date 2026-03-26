"""Pi 5 display power management via DDC/CI (ddcutil).

Pi 5 doesn't support vcgencmd display_power (Pi 4 only), and sysfs DPMS
writes fail because mpv holds the DRM master lock. DDC/CI communicates
with the monitor over I2C, which is completely independent of DRM.

Requires: sudo apt install ddcutil, i2c-dev module loaded.
"""

import logging
import subprocess

logger = logging.getLogger("cinegatto.display.pi")


class PiDisplay:
    """Controls monitor power via DDC/CI (ddcutil).

    VCP code 0xD6 (Power Mode):
      1 = On
      5 = Standby
    """

    def __init__(self):
        self._available = self._check_ddcutil()
        if self._available:
            logger.info("Display control via ddcutil (DDC/CI)")
        else:
            logger.warning("ddcutil not available — display power control disabled")

    def power_on(self) -> None:
        logger.debug("Display power ON")
        self._set_power(1)

    def power_off(self) -> None:
        logger.debug("Display power OFF (standby)")
        self._set_power(5)

    def _set_power(self, value: int) -> None:
        if not self._available:
            return
        try:
            result = subprocess.run(
                ["ddcutil", "setvcp", "d6", str(value)],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                logger.warning("ddcutil setvcp failed",
                               extra={"returncode": result.returncode, "value": value})
        except Exception:
            logger.exception("Failed to set display power to %d", value)

    def _check_ddcutil(self) -> bool:
        try:
            result = subprocess.run(
                ["ddcutil", "detect"],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False
        except Exception:
            return False
