"""Configuration loading with a two-layer merge chain.

Merge priority (highest wins):
  1. cinegatto.json (user-editable file at the repo root)
  2. DEFAULTS (hardcoded below)

The user only needs to specify overrides in cinegatto.json; any missing keys
fall back to DEFAULTS via dict.update(). This means a minimal config file
(e.g., just {"playlist_url": "..."}) is valid.

Validation is type-based: each key has a type predicate in VALIDATORS. This
catches config file typos (e.g., "audio": "yes" instead of true) early with
a clear error message, rather than failing cryptically at runtime.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("cinegatto.config")

# Every key here is a valid config option with its default value.
# The full set also serves as documentation of available settings.
DEFAULTS = {
    "playlist_url": "https://youtube.com/playlist?list=PLB3-YZ0bGxhkNJpgDdAUIxJYa842fsuMd",
    "api_port": 8080,
    "log_level": "info",
    "audio": False,
    "shuffle": True,
    "random_start": True,
    "mpv_extra_args": [],
    "watchdog_timeout_sec": 10,
    "log_ring_size": 10000,
    "log_file": ".cinegatto.log",
    "cache_enabled": True,
    "cache_path": "",
    "cache_disk_usage_pct": 80,
    "playlist_refresh_sec": 1800,
    "cache_format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "yt_cooldown_sec": 1800,
}

# Type validators — only check type, not semantic validity (e.g., port range).
# Keeping validators simple avoids rejecting configs that are technically valid
# but unusual (e.g., api_port=80 on a Pi running as root).
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
    "cache_disk_usage_pct": lambda v: isinstance(v, (int, float)),
    "playlist_refresh_sec": lambda v: isinstance(v, (int, float)),
    "cache_format": lambda v: isinstance(v, str),
    "yt_cooldown_sec": lambda v: isinstance(v, (int, float)),
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
    """Validate types of all known config keys.

    Only validates keys that exist in VALIDATORS — unknown keys from the
    config file are silently passed through. This is intentional: it allows
    forward-compatible config files that include keys for newer versions.
    """
    for key, check in VALIDATORS.items():
        if key in config and not check(config[key]):
            raise ConfigError(
                f"{key} has invalid type: expected {type(DEFAULTS[key]).__name__}, "
                f"got {type(config[key]).__name__}"
            )
    # Semantic bounds checks
    port = config.get("api_port", 8080)
    if port < 1 or port > 65535:
        raise ConfigError(f"api_port must be 1-65535, got {port}")
    if config.get("playlist_refresh_sec", 1800) < 10:
        raise ConfigError("playlist_refresh_sec must be >= 10")
    if config.get("watchdog_timeout_sec", 10) < 1:
        raise ConfigError("watchdog_timeout_sec must be >= 1")
    pct = config.get("cache_disk_usage_pct", 80)
    if pct < 1 or pct > 99:
        raise ConfigError("cache_disk_usage_pct must be 1-99")
