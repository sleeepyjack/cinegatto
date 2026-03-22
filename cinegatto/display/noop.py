"""No-op display for macOS / development."""

import logging

logger = logging.getLogger("cinegatto.display.noop")


class NoopDisplay:
    """No-op display implementation for non-Pi platforms."""

    def power_on(self) -> None:
        logger.debug("Display power_on (no-op)")

    def power_off(self) -> None:
        logger.debug("Display power_off (no-op)")
