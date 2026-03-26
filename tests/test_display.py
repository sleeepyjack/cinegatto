from unittest.mock import patch, call, mock_open

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
    @patch("cinegatto.display.pi._find_hdmi_dpms", return_value="/sys/class/drm/card1-HDMI-A-1/dpms")
    @patch("builtins.open", mock_open())
    def test_power_on_writes_dpms(self, mock_find):
        d = PiDisplay()
        d.power_on()
        open.assert_called_with("/sys/class/drm/card1-HDMI-A-1/dpms", "w")

    @patch("cinegatto.display.pi._find_hdmi_dpms", return_value="/sys/class/drm/card1-HDMI-A-1/dpms")
    @patch("builtins.open", mock_open())
    def test_power_off_writes_dpms(self, mock_find):
        d = PiDisplay()
        d.power_off()
        open.assert_called_with("/sys/class/drm/card1-HDMI-A-1/dpms", "w")

    @patch("cinegatto.display.pi._find_hdmi_dpms", return_value=None)
    def test_no_dpms_path_does_not_raise(self, _):
        d = PiDisplay()
        d.power_on()  # should not raise
        d.power_off()
