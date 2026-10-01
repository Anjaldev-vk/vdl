"""Configuration management and persistence for vdl."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from vdl.exceptions import ConfigError

logger = logging.getLogger(__name__)

VALID_QUALITIES = {"1080p", "720p", "480p", "best"}
VALID_AUDIO_FORMATS = {"mp3", "m4a", "opus", "best"}
VALID_NETWORK_MODES = {"direct", "tor"}


def get_default_download_dir() -> Path:
    """Return default download directory ~/Downloads/vdl."""
    return Path.home() / "Downloads" / "vdl"


def get_config_dir() -> Path:
    """Return platform-specific config directory for vdl."""
    if sys.platform == "win32":
        app_data = os.getenv("APPDATA")
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg_config = os.getenv("XDG_CONFIG_HOME")
        base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    return base / "vdl"


def get_config_path() -> Path:
    """Return the absolute path to config.json."""
    return get_config_dir() / "config.json"


def get_log_dir() -> Path:
    """Return platform-specific log directory for vdl."""
    if sys.platform == "win32":
        local_app_data = os.getenv("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "vdl" / "logs"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "vdl"
    else:
        xdg_state = os.getenv("XDG_STATE_HOME")
        if xdg_state:
            return Path(xdg_state) / "vdl" / "logs"
        return Path.home() / ".local" / "state" / "vdl" / "logs"


@dataclass
class Settings:
    """Application settings for vdl."""

    download_dir: Path = field(default_factory=get_default_download_dir)
    default_quality: str = "best"
    default_audio_format: str = "mp3"
    network_mode: str = "direct"
    tor_proxy: str = "socks5h://127.0.0.1:9050"
    retries: int = 3
    rate_limit: int | None = None
    restrict_filenames: bool = False
    write_thumbnail: bool = False
    noplaylist: bool = True
    windowsfilenames: bool = field(default_factory=lambda: sys.platform == "win32")

    @property
    def proxy(self) -> str | None:
        """Return the effective yt-dlp proxy option string based on network_mode."""
        return self.tor_proxy if self.network_mode == "tor" else None

    def __post_init__(self) -> None:
        if isinstance(self.download_dir, str):
            self.download_dir = Path(self.download_dir)
        self.download_dir = self.download_dir.expanduser()

        # Validate quality
        quality_pattern = re.compile(r"^\d{3,4}p$|^best$")
        if not quality_pattern.match(self.default_quality):
            raise ConfigError(
                f"Invalid default_quality '{self.default_quality}'. Must be 'best' or resolution like '1080p', '720p', '480p'."
            )

        # Validate audio format
        if self.default_audio_format not in VALID_AUDIO_FORMATS:
            raise ConfigError(
                f"Invalid default_audio_format '{self.default_audio_format}'. Valid options: {', '.join(sorted(VALID_AUDIO_FORMATS))}."
            )

        # Validate network mode
        if self.network_mode not in VALID_NETWORK_MODES:
            raise ConfigError(
                f"Invalid network_mode '{self.network_mode}'. Valid options: {', '.join(sorted(VALID_NETWORK_MODES))}."
            )

        if self.retries < 0:
            raise ConfigError("retries must be non-negative")

        if self.rate_limit is not None and self.rate_limit <= 0:
            raise ConfigError("rate_limit must be positive if set")


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from config.json, returning default Settings if missing or corrupt."""
    path = config_path or get_config_path()
    if not path.exists():
        return Settings()

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("Config file %s did not contain a JSON object. Using defaults.", path)
            return Settings()

        # Filter out unknown keys safely
        valid_fields = set(Settings.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return Settings(**filtered)
    except Exception as exc:
        logger.warning("Failed to load config from %s: %s. Reverting to safe defaults.", path, exc)
        return Settings()


def save_settings(settings: Settings, config_path: Path | None = None) -> Path:
    """Save settings atomically to config.json.

    Returns the path where settings were saved.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(settings)
    data["download_dir"] = str(settings.download_dir)

    # Atomic write via temp file in the same directory
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix="config_", suffix=".tmp")
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Replace destination atomically (on Windows, os.replace handles atomic overwrite in 3.3+)
        os.replace(tmp_path, path)
        return path
    except Exception as exc:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise ConfigError(f"Failed to save configuration to {path}: {exc}") from exc


VALID_CONFIG_KEYS = {
    "download_dir",
    "default_quality",
    "default_audio_format",
    "network_mode",
    "tor_proxy",
    "retries",
    "rate_limit",
    "restrict_filenames",
    "write_thumbnail",
    "noplaylist",
    "windowsfilenames",
}


def set_setting(settings: Settings, key: str, value: str) -> tuple[Settings, Any]:
    """Parse, validate, and update a specific configuration setting.

    Args:
        settings: Existing Settings instance.
        key: Setting field name.
        value: String representation of the new value.

    Returns:
        tuple (updated_settings, parsed_value).

    Raises:
        ConfigError: If key is unknown or value fails validation.
    """
    clean_key = key.strip().lower()
    if clean_key not in VALID_CONFIG_KEYS:
        raise ConfigError(
            f"Unknown configuration key '{key}'. Valid keys: {', '.join(sorted(VALID_CONFIG_KEYS))}."
        )

    parsed_value: Any
    if clean_key == "download_dir":
        parsed_value = Path(value).expanduser()
        settings.download_dir = parsed_value
    elif clean_key == "default_quality":
        val = value.strip().lower()
        pattern = re.compile(r"^\d{3,4}p$|^best$")
        if not pattern.match(val):
            raise ConfigError(
                f"Invalid default_quality '{value}'. Must match 'best' or resolution like '1080p', '720p', '480p'."
            )
        parsed_value = val
        settings.default_quality = val
    elif clean_key == "default_audio_format":
        val = value.strip().lower()
        if val not in VALID_AUDIO_FORMATS:
            raise ConfigError(
                f"Invalid default_audio_format '{value}'. Valid options: {', '.join(sorted(VALID_AUDIO_FORMATS))}."
            )
        parsed_value = val
        settings.default_audio_format = val
    elif clean_key == "network_mode":
        val = value.strip().lower()
        if val not in VALID_NETWORK_MODES:
            raise ConfigError(
                f"Invalid network_mode '{value}'. Valid options: {', '.join(sorted(VALID_NETWORK_MODES))}."
            )
        parsed_value = val
        settings.network_mode = val
    elif clean_key == "tor_proxy":
        val = value.strip()
        if not (
            val.startswith("socks5://") or val.startswith("socks5h://") or val.startswith("http://")
        ):
            raise ConfigError(
                f"Invalid tor_proxy '{value}'. Must start with socks5://, socks5h://, or http://."
            )
        parsed_value = val
        settings.tor_proxy = val
    elif clean_key == "retries":
        try:
            val_int = int(value)
            if val_int < 0:
                raise ValueError
        except ValueError:
            raise ConfigError(
                f"Invalid retries '{value}'. Must be a non-negative integer."
            ) from None
        parsed_value = val_int
        settings.retries = val_int
    elif clean_key == "rate_limit":
        val_lower = value.strip().lower()
        if val_lower in ("none", "null", "", "0"):
            parsed_value = None
            settings.rate_limit = None
        else:
            try:
                val_int = int(value)
                if val_int <= 0:
                    raise ValueError
            except ValueError:
                raise ConfigError(
                    f"Invalid rate_limit '{value}'. Must be a positive integer in bytes/sec, or 'none'."
                ) from None
            parsed_value = val_int
            settings.rate_limit = val_int
    elif clean_key in ("restrict_filenames", "write_thumbnail", "noplaylist", "windowsfilenames"):
        val_lower = value.strip().lower()
        if val_lower in ("true", "1", "yes", "on"):
            parsed_value = True
        elif val_lower in ("false", "0", "no", "off"):
            parsed_value = False
        else:
            raise ConfigError(f"Invalid boolean value '{value}' for {key}. Use true or false.")
        setattr(settings, clean_key, parsed_value)

    settings.__post_init__()
    return settings, parsed_value


def reset_settings(config_path: Path | None = None) -> Settings:
    """Reset configuration file to safe default values."""
    default_settings = Settings()
    save_settings(default_settings, config_path=config_path)
    return default_settings
