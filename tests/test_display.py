from unittest.mock import patch, MagicMock

from cinegatto.display.noop import NoopDisplay
from cinegatto.display.pi import PiDisplay


class TestNoopDisplay:
    def test_power_on_does_not_raise(self):
        d = NoopDisplay()
        d.power_on()

    def test_power_off_does_not_raise(self):
        d = NoopDisplay()
        d.power_off()


class TestPiDisplay:
    @patch("cinegatto.display.pi.PiDisplay._check_ddcutil", return_value=True)
    @patch("cinegatto.display.pi.subprocess.run")
    def test_power_on_calls_ddcutil(self, mock_run, _):
        d = PiDisplay()
        d.power_on()
        mock_run.assert_called_with(
            ["ddcutil", "setvcp", "d6", "1"],
            capture_output=True, timeout=10,
        )

    @patch("cinegatto.display.pi.PiDisplay._check_ddcutil", return_value=True)
    @patch("cinegatto.display.pi.subprocess.run")
    def test_power_off_calls_ddcutil_standby(self, mock_run, _):
        d = PiDisplay()
        d.power_off()
        mock_run.assert_called_with(
            ["ddcutil", "setvcp", "d6", "5"],
            capture_output=True, timeout=10,
        )

    @patch("cinegatto.display.pi.PiDisplay._check_ddcutil", return_value=False)
    def test_no_ddcutil_does_not_raise(self, _):
        d = PiDisplay()
        d.power_on()
        d.power_off()
