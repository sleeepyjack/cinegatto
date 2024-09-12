import subprocess
import sys

def turn_on_screen():
    """Turn on the display (screen)."""
    if sys.platform.startswith('linux'):
        # For Raspberry Pi or Linux systems
        try:
            # For systems with X server
            subprocess.call(['xset', 'dpms', 'force', 'on'])
        except FileNotFoundError:
            # For Raspberry Pi without X server
            subprocess.call(['vcgencmd', 'display_power', '1'])
    elif sys.platform == 'win32':
        # For Windows systems
        pass  # Typically, the screen will turn on automatically
    elif sys.platform == 'darwin':
        # For macOS systems
        pass  # The screen should wake on activity
    else:
        print("Screen standby not supported on this platform.")

def turn_off_screen():
    """Turn off the display (screen standby)."""
    if sys.platform.startswith('linux'):
        # For Raspberry Pi or Linux systems
        try:
            # For systems with X server
            subprocess.call(['xset', 'dpms', 'force', 'off'])
        except FileNotFoundError:
            # For Raspberry Pi without X server
            subprocess.call(['vcgencmd', 'display_power', '0'])
    elif sys.platform == 'win32':
        # For Windows systems
        import ctypes
        WM_SYSCOMMAND = 0x0112
        SC_MONITORPOWER = 0xF170
        HWND_BROADCAST = 0xFFFF
        ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
    elif sys.platform == 'darwin':
        # For macOS systems
        subprocess.call(['pmset', 'displaysleepnow'])
    else:
        print("Screen standby not supported on this platform.")