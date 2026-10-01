"""Unit tests for V9: Doctor diagnostics, rotating file logging, and enhanced error translation."""

import logging
from unittest.mock import patch

from typer.testing import CliRunner
from yt_dlp.utils import (
    DownloadError as YtDlpDownloadError,
)
from yt_dlp.utils import (
    ExtractorError as YtDlpExtractorError,
)
from yt_dlp.utils import (
    GeoRestrictedError as YtDlpGeoRestrictedError,
)

from vdl.cli import app, configure_logging
from vdl.config import Settings
from vdl.doctor import (
    CheckStatus,
    run_doctor,
    run_doctor_checks,
)
from vdl.downloader import _translate_ytdlp_error
from vdl.exceptions import (
    ExtractorError,
    GeoRestrictedError,
    LoginRequiredError,
)


def test_doctor_all_checks_pass(tmp_path):
    """vdl doctor exits 0 when all checks pass."""
    settings = Settings(download_dir=tmp_path)
    with patch("vdl.doctor.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with patch("vdl.doctor._run_quick_cmd", return_value="7.1"):
            with patch("vdl.doctor.shutil.which", side_effect=lambda x: f"C:\\{x}.exe"):
                with patch("vdl.doctor.is_proxy_reachable", return_value=True):
                    code = run_doctor(settings=settings)
                    assert code == 0


def test_doctor_critical_failure_exits_1(tmp_path):
    """vdl doctor exits 1 when a critical check (like FFmpeg) fails."""
    settings = Settings(download_dir=tmp_path)
    with patch("vdl.doctor.get_ffmpeg_path", return_value=None):
        code = run_doctor(settings=settings)
        assert code == 1


def test_doctor_warn_only_exits_0(tmp_path):
    """vdl doctor exits 0 when all critical checks pass and non-critical checks produce WARN."""
    settings = Settings(download_dir=tmp_path)
    with patch("vdl.doctor.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with patch("vdl.doctor._run_quick_cmd", return_value="7.1"):
            # Tor binary missing (WARN), Tor proxy unreachable (WARN), Node only (WARN)
            def mock_which(cmd):
                if cmd == "node":
                    return "C:\\node.exe"
                return None

            with patch("vdl.doctor.shutil.which", side_effect=mock_which):
                with patch("vdl.doctor.is_proxy_reachable", return_value=False):
                    results = run_doctor_checks(settings=settings)
                    statuses = {r.name: r.status for r in results}
                    assert statuses["FFmpeg"] == CheckStatus.OK
                    assert statuses["Tor Binary"] == CheckStatus.WARN
                    assert statuses["Tor Proxy"] == CheckStatus.WARN
                    assert statuses["JS Runtime"] == CheckStatus.WARN

                    code = run_doctor(settings=settings)
                    assert code == 0


def test_doctor_cli_command(tmp_path):
    """Invoking 'vdl doctor' via CLI runs diagnostics and produces formatted output."""
    runner = CliRunner()
    with patch("vdl.doctor.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with patch("vdl.doctor.shutil.which", return_value="C:\\deno.exe"):
            with patch("vdl.doctor._run_quick_cmd", return_value="deno 2.0.0"):
                result = runner.invoke(app, ["doctor"])
                assert result.exit_code == 0
                assert "Doctor" in result.output
                assert "Component" in result.output
                assert "Python" in result.output
                assert "FFmpeg" in result.output


def test_rotating_file_handler_captures_info_regardless_of_console_flags(tmp_path):
    """Rotating file logger records INFO events even when console is set to WARNING."""
    log_file = tmp_path / "vdl.log"

    with patch("vdl.cli.get_log_dir", return_value=tmp_path):
        # Configure logging with default flags (verbose=False, debug=False)
        configure_logging(verbose=False, debug=False)

        test_logger = logging.getLogger("vdl.test_worker")
        test_logger.info("Operational milestone recorded to log file")
        test_logger.warning("Warning message for user and log")

        assert log_file.exists()
        log_content = log_file.read_text(encoding="utf-8")
        assert "Operational milestone recorded to log file" in log_content
        assert "Warning message for user and log" in log_content


def test_translate_geo_restricted_error():
    """Geo-restricted yt-dlp error is translated to GeoRestrictedError with hint."""
    exc1 = YtDlpGeoRestrictedError("Video is not available in your country")
    translated1 = _translate_ytdlp_error(exc1, "https://example.com/geo-video")
    assert isinstance(translated1, GeoRestrictedError)
    assert "geographically restricted" in translated1.message.lower()
    assert "tor" in translated1.message.lower() or "vpn" in translated1.message.lower()

    exc2 = YtDlpDownloadError(
        "ERROR: The uploader has not made this video available in your country"
    )
    translated2 = _translate_ytdlp_error(exc2, "https://example.com/geo-video2")
    assert isinstance(translated2, GeoRestrictedError)


def test_translate_login_required_error():
    """Age-restricted or login-required error is translated to LoginRequiredError."""
    exc1 = YtDlpDownloadError("ERROR: Sign in to confirm your age. This video may be inappropriate")
    translated1 = _translate_ytdlp_error(exc1, "https://example.com/age-video")
    assert isinstance(translated1, LoginRequiredError)
    assert "authentication required" in translated1.message.lower()
    assert "cookies" in translated1.message.lower()

    exc2 = YtDlpDownloadError("ERROR: Private video. Sign in if you've been granted access")
    translated2 = _translate_ytdlp_error(exc2, "https://example.com/private-video")
    assert isinstance(translated2, LoginRequiredError)


def test_translate_extractor_error():
    """Generic yt-dlp ExtractorError translates to domain ExtractorError."""
    exc = YtDlpExtractorError("Failed to parse player JavaScript response")
    translated = _translate_ytdlp_error(exc, "https://example.com/video")
    assert isinstance(translated, ExtractorError)
    assert "metadata extraction failed" in translated.message.lower()
