from unittest.mock import patch, call

from cinegatto.display.noop import NoopDisplay
from cinegatto.display.pi import PiDisplay


class TestNoopDisplay:
    def test_power_on_does_not_raise(self):
        d = NoopDisplay()
        d.power_on()  # should not raise

    def test_power_off_does_not_raise(self):
        d = NoopDisplay()
        d.power_off()  # should not raise


class TestPiDisplay:
    @patch("cinegatto.display.pi.subprocess.run")
    def test_power_on_calls_vcgencmd(self, mock_run):
        d = PiDisplay()
        d.power_on()
        mock_run.assert_called_once_with(["vcgencmd", "display_power", "1"], check=True)

    @patch("cinegatto.display.pi.subprocess.run")
    def test_power_off_calls_vcgencmd(self, mock_run):
        d = PiDisplay()
        d.power_off()
        mock_run.assert_called_once_with(["vcgencmd", "display_power", "0"], check=True)
