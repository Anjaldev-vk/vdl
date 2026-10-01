"""Unit tests for V6 configuration management, persistence, atomic writes, and CLI commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from vdl.cli import app
from vdl.config import (
    ConfigError,
    Settings,
    get_config_path,
    get_default_download_dir,
    get_log_dir,
    load_settings,
    reset_settings,
    save_settings,
    set_setting,
)


def test_default_settings():
    """Verify default Settings values."""
    s = Settings()
    assert s.download_dir == get_default_download_dir()
    assert s.default_quality == "best"
    assert s.default_audio_format == "mp3"
    assert s.network_mode == "direct"
    assert "9050" in s.tor_proxy
    assert s.retries == 3
    assert s.rate_limit is None
    assert s.restrict_filenames is False
    assert s.write_thumbnail is False
    assert s.noplaylist is True


def test_config_and_log_paths_exist():
    """Verify get_config_path() and get_log_dir() return valid Path instances."""
    cfg = get_config_path()
    assert isinstance(cfg, Path)
    assert cfg.name == "config.json"

    log_dir = get_log_dir()
    assert isinstance(log_dir, Path)
    assert "vdl" in str(log_dir).lower()


def test_load_and_save_valid_settings(tmp_path):
    """Save settings and load them back, verifying round-trip persistence."""
    cfg_file = tmp_path / "config.json"
    s = Settings(
        download_dir=tmp_path / "custom",
        default_quality="720p",
        default_audio_format="opus",
        network_mode="tor",
        retries=5,
        rate_limit=1048576,
        restrict_filenames=True,
    )
    save_settings(s, config_path=cfg_file)
    assert cfg_file.exists()

    loaded = load_settings(config_path=cfg_file)
    assert loaded.download_dir == tmp_path / "custom"
    assert loaded.default_quality == "720p"
    assert loaded.default_audio_format == "opus"
    assert loaded.network_mode == "tor"
    assert loaded.retries == 5
    assert loaded.rate_limit == 1048576
    assert loaded.restrict_filenames is True


def test_load_corrupt_json_falls_back_to_defaults(tmp_path, caplog):
    """Corrupt or truncated JSON logs a warning and falls back to safe defaults without crashing."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"download_dir": "broken/json', encoding="utf-8")

    loaded = load_settings(config_path=cfg_file)
    assert isinstance(loaded, Settings)
    assert loaded.default_quality == "best"
    assert "Failed to load config" in caplog.text


def test_load_non_dict_json_falls_back_to_defaults(tmp_path, caplog):
    """JSON that is not a dictionary logs a warning and falls back to defaults."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('["not", "a", "dict"]', encoding="utf-8")

    loaded = load_settings(config_path=cfg_file)
    assert isinstance(loaded, Settings)
    assert "did not contain a JSON object" in caplog.text


def test_atomic_write_survives_crash(tmp_path):
    """Atomic write protects original file if an exception occurs mid-write."""
    cfg_file = tmp_path / "config.json"
    original_settings = Settings(default_quality="1080p")
    save_settings(original_settings, config_path=cfg_file)

    # Simulate an error during json.dump
    new_settings = Settings(default_quality="480p")
    with patch("json.dump", side_effect=OSError("Simulated disk error")):
        with pytest.raises(ConfigError):
            save_settings(new_settings, config_path=cfg_file)

    # Original file is preserved untouched
    loaded = load_settings(config_path=cfg_file)
    assert loaded.default_quality == "1080p"


def test_set_setting_valid_fields(tmp_path):
    """Verify set_setting accurately parses and sets every supported field."""
    s = Settings()

    s, val = set_setting(s, "download_dir", str(tmp_path / "vids"))
    assert s.download_dir == tmp_path / "vids"

    s, val = set_setting(s, "default_quality", "720p")
    assert s.default_quality == "720p"

    s, val = set_setting(s, "default_audio_format", "m4a")
    assert s.default_audio_format == "m4a"

    s, val = set_setting(s, "network_mode", "tor")
    assert s.network_mode == "tor"

    s, val = set_setting(s, "tor_proxy", "socks5://127.0.0.1:9150")
    assert s.tor_proxy == "socks5://127.0.0.1:9150"

    s, val = set_setting(s, "retries", "10")
    assert s.retries == 10

    s, val = set_setting(s, "rate_limit", "500000")
    assert s.rate_limit == 500000

    s, val = set_setting(s, "rate_limit", "none")
    assert s.rate_limit is None

    s, val = set_setting(s, "restrict_filenames", "true")
    assert s.restrict_filenames is True

    s, val = set_setting(s, "write_thumbnail", "1")
    assert s.write_thumbnail is True

    s, val = set_setting(s, "noplaylist", "false")
    assert s.noplaylist is False


def test_set_setting_invalid_values():
    """Verify invalid values for each field raise clear ConfigError."""
    s = Settings()

    # Unknown key
    with pytest.raises(ConfigError) as exc:
        set_setting(s, "unknown_field", "value")
    assert "Unknown configuration key" in str(exc.value)

    # Invalid quality
    with pytest.raises(ConfigError) as exc:
        set_setting(s, "default_quality", "invalid_res")
    assert "Invalid default_quality" in str(exc.value)

    # Invalid audio format
    with pytest.raises(ConfigError) as exc:
        set_setting(s, "default_audio_format", "flac")
    assert "Invalid default_audio_format" in str(exc.value)

    # Invalid network mode
    with pytest.raises(ConfigError) as exc:
        set_setting(s, "network_mode", "vpn")
    assert "Invalid network_mode" in str(exc.value)

    # Invalid retries
    with pytest.raises(ConfigError) as exc:
        set_setting(s, "retries", "-1")
    assert "non-negative integer" in str(exc.value)

    # Invalid boolean
    with pytest.raises(ConfigError) as exc:
        set_setting(s, "restrict_filenames", "maybe")
    assert "Invalid boolean value" in str(exc.value)


def test_reset_settings(tmp_path):
    """Verify reset_settings resets to default configuration."""
    cfg_file = tmp_path / "config.json"
    modified = Settings(default_quality="480p", retries=9)
    save_settings(modified, config_path=cfg_file)

    reset = reset_settings(config_path=cfg_file)
    assert reset.default_quality == "best"
    assert reset.retries == 3

    loaded = load_settings(config_path=cfg_file)
    assert loaded.default_quality == "best"


# --- CLI config commands ---


def test_cli_config_path():
    """Verify vdl config path outputs the configuration file location."""
    runner = CliRunner()
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert "config.json" in result.output


def test_cli_config_show():
    """Verify vdl config show displays settings table."""
    runner = CliRunner()
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "vdl Configuration" in result.output
    assert "default_quality" in result.output
    assert "download_dir" in result.output


def test_cli_config_set_and_reset(tmp_path):
    """Verify vdl config set updates settings and vdl config reset restores them."""
    cfg_file = tmp_path / "config.json"
    runner = CliRunner()

    with patch("vdl.cli.get_config_path", return_value=cfg_file):
        with patch("vdl.config.get_config_path", return_value=cfg_file):
            # 1. Set default_quality to 720p
            set_res = runner.invoke(app, ["config", "set", "default_quality", "720p"])
            assert set_res.exit_code == 0
            assert "default_quality = 720p" in set_res.output

            loaded = load_settings(config_path=cfg_file)
            assert loaded.default_quality == "720p"

            # 2. Reset config
            reset_res = runner.invoke(app, ["config", "reset"])
            assert reset_res.exit_code == 0
            assert "reset to default" in reset_res.output

            loaded_reset = load_settings(config_path=cfg_file)
            assert loaded_reset.default_quality == "best"


def test_cli_config_set_invalid_key_fails():
    """Verify vdl config set with invalid key fails with friendly error."""
    runner = CliRunner()
    result = runner.invoke(app, ["config", "set", "bogus_key", "val"])
    assert result.exit_code != 0
    assert "Unknown configuration key" in result.output


def test_cli_flags_override_config_precedence(tmp_path):
    """Verify CLI flag overrides config values for that invocation without persisting."""
    cfg_file = tmp_path / "config.json"
    # Config file has download_dir = tmp_path / "default_dir"
    default_dir = tmp_path / "default_dir"
    save_settings(Settings(download_dir=default_dir), config_path=cfg_file)

    runner = CliRunner()
    cli_dir = tmp_path / "cli_override_dir"

    with patch("vdl.cli.get_config_path", return_value=cfg_file):
        with patch("vdl.config.get_config_path", return_value=cfg_file):
            with patch("vdl.cli.download_video") as mock_dl:
                mock_dl.return_value = MagicMock(title="Video", filepath=cli_dir / "vid.mp4")

                result = runner.invoke(
                    app,
                    ["download", "https://example.com/watch?v=123", "-o", str(cli_dir)],
                )
                assert result.exit_code == 0

                # Verify download_video received the CLI override dir
                called_settings = mock_dl.call_args[1]["settings"]
                assert called_settings.download_dir == cli_dir

                # Verify config file on disk was NOT modified (did not persist CLI override)
                persisted = load_settings(config_path=cfg_file)
                assert persisted.download_dir == default_dir
