import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("cinegatto.config")

DEFAULTS = {
    "playlist_url": "https://youtube.com/playlist?list=PLB3-YZ0bGxhkNJpgDdAUIxJYa842fsuMd",
    "api_port": 8080,
    "log_level": "debug",
    "audio": False,
    "mpv_extra_args": [],
    "watchdog_timeout_sec": 10,
    "log_ring_size": 500,
}

VALIDATORS = {
    "playlist_url": lambda v: isinstance(v, str),
    "api_port": lambda v: isinstance(v, int),
    "log_level": lambda v: isinstance(v, str),
    "audio": lambda v: isinstance(v, bool),
    "mpv_extra_args": lambda v: isinstance(v, list),
    "watchdog_timeout_sec": lambda v: isinstance(v, (int, float)),
    "log_ring_size": lambda v: isinstance(v, int),
}


class ConfigError(Exception):
    pass


def load_config(user_config_path=None, default_config_dir=None):
    """Load config by merging defaults with optional user overrides.

    Priority: user config > default.json file > hardcoded DEFAULTS.
    """
    config = dict(DEFAULTS)

    # Load default.json from config dir if it exists
    if default_config_dir is None:
        default_config_dir = str(Path(__file__).parent.parent / "config")
    default_file = os.path.join(default_config_dir, "default.json")
    if os.path.isfile(default_file):
        config.update(_load_json(default_file))

    # Load user config if provided and exists
    if user_config_path is not None:
        if os.path.isfile(user_config_path):
            config.update(_load_json(user_config_path))
        else:
            logger.debug("User config not found at %s, using defaults", user_config_path)

    _validate(config)
    logger.debug("Config loaded", extra={"config": config})
    return config


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}") from e


def _validate(config):
    for key, check in VALIDATORS.items():
        if key in config and not check(config[key]):
            raise ConfigError(
                f"{key} has invalid type: expected {type(DEFAULTS[key]).__name__}, "
                f"got {type(config[key]).__name__}"
            )
