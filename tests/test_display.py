import subprocess
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
    @patch("cinegatto.display.pi._find_hdmi_dpms", return_value="/sys/class/drm/card1-HDMI-A-1/dpms")
    @patch("cinegatto.display.pi.subprocess.run")
    def test_power_on_writes_dpms(self, mock_run, _):
        d = PiDisplay()
        d.power_on()
        mock_run.assert_called_once_with(
            ["sudo", "tee", "/sys/class/drm/card1-HDMI-A-1/dpms"],
            input=b"On", stdout=subprocess.DEVNULL, check=True,
        )

    @patch("cinegatto.display.pi._find_hdmi_dpms", return_value="/sys/class/drm/card1-HDMI-A-1/dpms")
    @patch("cinegatto.display.pi.subprocess.run")
    def test_power_off_writes_dpms(self, mock_run, _):
        d = PiDisplay()
        d.power_off()
        mock_run.assert_called_once_with(
            ["sudo", "tee", "/sys/class/drm/card1-HDMI-A-1/dpms"],
            input=b"Off", stdout=subprocess.DEVNULL, check=True,
        )

    @patch("cinegatto.display.pi._find_hdmi_dpms", return_value=None)
    def test_no_dpms_path_does_not_raise(self, _):
        d = PiDisplay()
        d.power_on()  # should not raise
        d.power_off()
