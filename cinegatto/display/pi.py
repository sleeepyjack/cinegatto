"""Pi display power management via vcgencmd."""

import logging
import subprocess

logger = logging.getLogger("cinegatto.display.pi")


class PiDisplay:
    """Controls HDMI display power on Raspberry Pi via vcgencmd."""

    def power_on(self) -> None:
        logger.info("Display power ON")
        subprocess.run(["vcgencmd", "display_power", "1"], check=True)

    def power_off(self) -> None:
        logger.info("Display power OFF")
        subprocess.run(["vcgencmd", "display_power", "0"], check=True)
