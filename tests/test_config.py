import json
import os

import pytest

from cinegatto.config import load_config, ConfigError


class TestLoadConfig:
    def test_load_defaults_when_no_user_config(self, tmp_path):
        """Loading with no user config file returns all defaults."""
        config = load_config(user_config_path=None, default_config_dir=str(tmp_path))
        assert config["api_port"] == 8080
        assert config["log_level"] == "debug"
        assert config["audio"] is False
        assert config["mpv_extra_args"] == []
        assert config["watchdog_timeout_sec"] == 10
        assert config["log_ring_size"] == 500

    def test_load_default_config_from_file(self, tmp_path):
        """Loads defaults from the default.json file in the config dir."""
        default = {"playlist_url": "", "api_port": 9090, "log_level": "info",
                    "audio": False, "mpv_extra_args": [], "watchdog_timeout_sec": 10,
                    "log_ring_size": 500}
        (tmp_path / "default.json").write_text(json.dumps(default))
        config = load_config(user_config_path=None, default_config_dir=str(tmp_path))
        assert config["api_port"] == 9090

    def test_user_config_overrides_defaults(self, tmp_path):
        """User config values override defaults."""
        user_file = tmp_path / "user.json"
        user_file.write_text(json.dumps({"api_port": 3000, "audio": True}))
        config = load_config(user_config_path=str(user_file))
        assert config["api_port"] == 3000
        assert config["audio"] is True
        # Non-overridden values come from defaults
        assert config["log_level"] == "debug"

    def test_missing_user_config_file_uses_defaults(self):
        """A nonexistent user config path falls back to defaults without error."""
        config = load_config(user_config_path="/nonexistent/config.json")
        assert config["api_port"] == 8080

    def test_invalid_json_raises_config_error(self, tmp_path):
        """Invalid JSON in user config raises ConfigError."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json")
        with pytest.raises(ConfigError, match="Invalid JSON"):
            load_config(user_config_path=str(bad_file))

    def test_playlist_url_is_string(self, tmp_path):
        """playlist_url must be a string."""
        user_file = tmp_path / "user.json"
        user_file.write_text(json.dumps({"playlist_url": 12345}))
        with pytest.raises(ConfigError, match="playlist_url"):
            load_config(user_config_path=str(user_file))

    def test_api_port_must_be_int(self, tmp_path):
        """api_port must be an integer."""
        user_file = tmp_path / "user.json"
        user_file.write_text(json.dumps({"api_port": "not_a_port"}))
        with pytest.raises(ConfigError, match="api_port"):
            load_config(user_config_path=str(user_file))

    def test_config_returns_copy(self):
        """Returned config is a dict (not a reference to internal state)."""
        config = load_config()
        config["api_port"] = 9999
        config2 = load_config()
        assert config2["api_port"] == 8080
