"""Custom exceptions for the vdl package."""

from __future__ import annotations


class VdlError(Exception):
    """Base exception for all vdl-specific errors."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code

    def __str__(self) -> str:
        return self.message


class DownloadError(VdlError):
    """Raised when media download or extraction fails."""


class UnsupportedUrlError(VdlError):
    """Raised when the provided URL is not supported by any extractor."""


class FFmpegNotFoundError(VdlError):
    """Raised when FFmpeg is required for an operation but cannot be found."""


class NetworkError(VdlError):
    """Raised when a network operation or proxy connection fails."""


class ConfigError(VdlError):
    """Raised when configuration loading, validation, or persistence fails."""


class FormatNotFoundError(VdlError):
    """Raised when a requested format or resolution cannot be found."""


class GeoRestrictedError(DownloadError):
    """Raised when media is unavailable in the current geographic region."""


class LoginRequiredError(DownloadError):
    """Raised when media requires user authentication, premium subscription, or age confirmation."""


class ExtractorError(DownloadError):
    """Raised when metadata extraction fails for a URL."""
