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
    "shuffle": True,
    "random_start": True,
    "mpv_extra_args": [],
    "watchdog_timeout_sec": 10,
    "log_ring_size": 500,
    "log_file": ".cinegatto.log",
    "cache_enabled": True,
    "cache_path": "",
    "cache_max_size_gb": 16,
    "playlist_refresh_sec": 1800,
    "cache_format": "bestvideo[height<=720]+bestaudio/best[height<=720]",
}

VALIDATORS = {
    "playlist_url": lambda v: isinstance(v, str),
    "api_port": lambda v: isinstance(v, int),
    "log_level": lambda v: isinstance(v, str),
    "audio": lambda v: isinstance(v, bool),
    "shuffle": lambda v: isinstance(v, bool),
    "random_start": lambda v: isinstance(v, bool),
    "mpv_extra_args": lambda v: isinstance(v, list),
    "watchdog_timeout_sec": lambda v: isinstance(v, (int, float)),
    "log_ring_size": lambda v: isinstance(v, int),
    "log_file": lambda v: isinstance(v, str),
    "cache_enabled": lambda v: isinstance(v, bool),
    "cache_path": lambda v: isinstance(v, str),
    "cache_max_size_gb": lambda v: isinstance(v, (int, float)),
    "playlist_refresh_sec": lambda v: isinstance(v, (int, float)),
    "cache_format": lambda v: isinstance(v, str),
}


class ConfigError(Exception):
    pass


def load_config(config_path=None):
    """Load config by merging hardcoded defaults with cinegatto.json.

    Priority: config file > hardcoded DEFAULTS.
    Looks for cinegatto.json at the repo root by default.
    """
    config = dict(DEFAULTS)

    # Resolve config file path
    if config_path is None:
        config_path = str(Path(__file__).parent.parent / "cinegatto.json")

    if os.path.isfile(config_path):
        config.update(_load_json(config_path))
        logger.debug("Loaded config from %s", config_path)
    else:
        logger.debug("No config file at %s, using defaults", config_path)

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
