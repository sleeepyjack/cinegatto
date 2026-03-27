import json
import os

import pytest

from cinegatto.config import load_config, ConfigError


class TestLoadConfig:
    def test_load_defaults_when_no_config_file(self, tmp_path):
        """Loading with a nonexistent config file returns all defaults."""
        config = load_config(config_path=str(tmp_path / "nonexistent.json"))
        assert config["api_port"] == 8080
        assert config["log_level"] == "info"
        assert config["audio"] is False
        assert config["mpv_extra_args"] == []
        assert config["watchdog_timeout_sec"] == 10
        assert config["log_ring_size"] == 10000

    def test_load_config_from_file(self, tmp_path):
        """Loads values from a config file."""
        cfg_file = tmp_path / "cinegatto.json"
        cfg_file.write_text(json.dumps({"api_port": 9090, "log_level": "info"}))
        config = load_config(config_path=str(cfg_file))
        assert config["api_port"] == 9090
        assert config["log_level"] == "info"

    def test_config_file_overrides_defaults(self, tmp_path):
        """Config file values override hardcoded defaults."""
        cfg_file = tmp_path / "cinegatto.json"
        cfg_file.write_text(json.dumps({"api_port": 3000, "audio": True}))
        config = load_config(config_path=str(cfg_file))
        assert config["api_port"] == 3000
        assert config["audio"] is True
        # Non-overridden values come from defaults
        assert config["log_level"] == "info"

    def test_missing_config_file_uses_defaults(self):
        """A nonexistent config path falls back to defaults without error."""
        config = load_config(config_path="/nonexistent/config.json")
        assert config["api_port"] == 8080

    def test_invalid_json_raises_config_error(self, tmp_path):
        """Invalid JSON in config raises ConfigError."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json")
        with pytest.raises(ConfigError, match="Invalid JSON"):
            load_config(config_path=str(bad_file))

    def test_playlist_url_is_string(self, tmp_path):
        """playlist_url must be a string."""
        cfg_file = tmp_path / "cinegatto.json"
        cfg_file.write_text(json.dumps({"playlist_url": 12345}))
        with pytest.raises(ConfigError, match="playlist_url"):
            load_config(config_path=str(cfg_file))

    def test_api_port_must_be_int(self, tmp_path):
        """api_port must be an integer."""
        cfg_file = tmp_path / "cinegatto.json"
        cfg_file.write_text(json.dumps({"api_port": "not_a_port"}))
        with pytest.raises(ConfigError, match="api_port"):
            load_config(config_path=str(cfg_file))

    def test_config_returns_copy(self):
        """Returned config is a dict (not a reference to internal state)."""
        config = load_config()
        config["api_port"] = 9999
        config2 = load_config()
        assert config2["api_port"] == 8080

    def test_api_port_out_of_range(self, tmp_path):
        cfg = tmp_path / "cinegatto.json"
        cfg.write_text(json.dumps({"api_port": 70000}))
        with pytest.raises(ConfigError, match="api_port"):
            load_config(config_path=str(cfg))

    def test_api_port_zero(self, tmp_path):
        cfg = tmp_path / "cinegatto.json"
        cfg.write_text(json.dumps({"api_port": 0}))
        with pytest.raises(ConfigError, match="api_port"):
            load_config(config_path=str(cfg))

    def test_playlist_refresh_too_small(self, tmp_path):
        cfg = tmp_path / "cinegatto.json"
        cfg.write_text(json.dumps({"playlist_refresh_sec": 5}))
        with pytest.raises(ConfigError, match="playlist_refresh_sec"):
            load_config(config_path=str(cfg))

    def test_cache_disk_usage_pct_out_of_range(self, tmp_path):
        cfg = tmp_path / "cinegatto.json"
        cfg.write_text(json.dumps({"cache_disk_usage_pct": 100}))
        with pytest.raises(ConfigError, match="cache_disk_usage_pct"):
            load_config(config_path=str(cfg))
