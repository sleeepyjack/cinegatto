from typing import Protocol


class Display(Protocol):
    """Protocol for monitor power management."""

    def power_on(self) -> None:
        """Turn the display on."""
        ...

    def power_off(self) -> None:
        """Turn the display off / enter standby."""
        ...
