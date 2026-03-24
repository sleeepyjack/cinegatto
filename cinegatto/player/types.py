from typing import Protocol, Optional, Any


class PlayerState:
    """Snapshot of current player state."""

    def __init__(self, playing=False, video_url=None, video_title=None,
                 position=0.0, duration=0.0):
        self.playing = playing
        self.video_url = video_url
        self.video_title = video_title
        self.position = position
        self.duration = duration

    def to_dict(self):
        return {
            "playing": self.playing,
            "video_url": self.video_url,
            "video_title": self.video_title,
            "position": self.position,
            "duration": self.duration,
        }


class Player(Protocol):
    """Protocol for video player implementations."""

    def load_video(self, url: str, start_percent: float = None) -> None:
        """Load and start playing a video URL. Optional start position as percentage."""
        ...

    def play(self) -> None:
        """Resume playback."""
        ...

    def pause(self) -> None:
        """Pause playback."""
        ...

    def seek(self, position: float) -> None:
        """Seek to an absolute position in seconds."""
        ...

    def show_video(self, visible: bool) -> None:
        """Show or black out the video. Overlays remain visible."""
        ...

    def get_state(self) -> PlayerState:
        """Return current player state."""
        ...

    def shutdown(self) -> None:
        """Gracefully shut down the player."""
        ...
