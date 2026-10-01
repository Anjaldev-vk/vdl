"""Unit tests for V4 audio extraction, stream-copy decisions, FFmpeg requirements, and CLI."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner
from yt_dlp.utils import UnsupportedError

from vdl.cli import app
from vdl.config import Settings
from vdl.downloader import (
    DownloadResult,
    build_audio_format_selector,
    download_audio,
    should_stream_copy,
)
from vdl.exceptions import FFmpegNotFoundError, FormatNotFoundError

# --- Stream copy vs re-encode decision tests ---


def test_stream_copy_vs_reencode_decision():
    """Verify should_stream_copy decisions across all target and source codec combinations."""
    # m4a target
    assert should_stream_copy("m4a", "mp4a.40.2") is True
    assert should_stream_copy("m4a", "mp4a") is True
    assert should_stream_copy("m4a", "aac") is True
    assert should_stream_copy("m4a", "opus") is False
    assert should_stream_copy("m4a", "vp9") is False

    # opus target
    assert should_stream_copy("opus", "opus") is True
    assert should_stream_copy("opus", "mp4a.40.2") is False
    assert should_stream_copy("opus", "aac") is False

    # mp3 target (always re-encode unless source is natively mp3)
    assert should_stream_copy("mp3", "opus") is False
    assert should_stream_copy("mp3", "mp4a.40.2") is False
    assert should_stream_copy("mp3", "mp3") is True

    # best target (keeps native stream)
    assert should_stream_copy("best", "opus") is True
    assert should_stream_copy("best", "mp4a.40.2") is True
    assert should_stream_copy("best", "anything") is True

    # Missing or empty codecs
    assert should_stream_copy("m4a", None) is False
    assert should_stream_copy("m4a", "none") is False
    assert should_stream_copy("opus", "") is False


# --- Format selector for audio ---


def test_build_audio_format_selector():
    """Verify audio format selector prioritizes streams that match target codec."""
    # m4a prioritizes AAC/mp4a streams
    m4a_sel = build_audio_format_selector("m4a")
    assert "ba[ext=m4a]" in m4a_sel
    assert "acodec^=mp4a" in m4a_sel

    # opus prioritizes opus streams
    opus_sel = build_audio_format_selector("opus")
    assert "acodec^=opus" in opus_sel

    # mp3 and best use generic best audio
    assert build_audio_format_selector("mp3") == "ba/b"
    assert build_audio_format_selector("best") == "ba/b"


# --- Postprocessor configuration ---


@pytest.mark.parametrize("fmt", ["mp3", "m4a", "opus", "best"])
def test_postprocessor_config_per_format(tmp_path, fmt):
    """Verify FFmpegExtractAudio postprocessor is properly configured for each target format."""
    settings = Settings(download_dir=tmp_path)
    target_file = tmp_path / f"audio.{fmt if fmt != 'best' else 'm4a'}"
    target_file.touch()

    with patch("vdl.downloader.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = {
                "id": "123",
                "title": "Audio Track",
                "requested_downloads": [{"filepath": str(target_file)}],
            }

            res = download_audio(
                url="https://example.com/audio",
                settings=settings,
                audio_format=fmt,
            )

            assert isinstance(res, DownloadResult)
            assert res.filepath == target_file

            called_opts = mock_ydl_cls.call_args[0][0]
            pps = called_opts.get("postprocessors", [])
            assert len(pps) == 1
            assert pps[0]["key"] == "FFmpegExtractAudio"
            assert pps[0]["preferredcodec"] == fmt
            if fmt == "mp3":
                assert pps[0].get("preferredquality") == "0"
            else:
                assert "preferredquality" not in pps[0]


# --- Missing FFmpeg raises before any yt-dlp call ---


def test_missing_ffmpeg_raises_before_any_ytdlp_call(tmp_path):
    """When FFmpeg is missing, download_audio raises FFmpegNotFoundError before initializing yt-dlp."""
    settings = Settings(download_dir=tmp_path)
    with patch("vdl.media.shutil.which", return_value=None):
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            with pytest.raises(FFmpegNotFoundError) as exc_info:
                download_audio(
                    url="https://example.com/audio",
                    settings=settings,
                    audio_format="mp3",
                )

            assert "FFmpeg is required" in exc_info.value.message
            assert "install" in exc_info.value.message.lower()
            mock_ydl.assert_not_called()


# --- Tor fail closed ---


def test_audio_tor_unreachable_fails_closed(tmp_path):
    """vdl audio --network tor with unreachable proxy fails closed without calling yt-dlp."""
    runner = CliRunner()
    with patch("socket.create_connection", side_effect=OSError("Connection refused")):
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            result = runner.invoke(
                app,
                [
                    "audio",
                    "https://example.com/watch?v=123",
                    "--network",
                    "tor",
                    "-o",
                    str(tmp_path),
                ],
            )
            assert result.exit_code != 0
            assert "unreachable" in result.output.lower() or "fail closed" in result.output.lower()
            mock_ydl.assert_not_called()


# --- Filepath resolution and verification ---


def test_audio_filepath_resolved_from_requested_downloads(tmp_path):
    """Verify final audio filepath is taken from requested_downloads and verified on disk."""
    settings = Settings(download_dir=tmp_path)
    extracted_file = tmp_path / "Song [123].opus"
    extracted_file.touch()

    with patch("vdl.downloader.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = {
                "id": "123",
                "title": "Song",
                "requested_downloads": [{"filepath": str(extracted_file)}],
            }

            res = download_audio(
                url="https://example.com/watch?v=123",
                settings=settings,
                audio_format="opus",
            )
            assert res.filepath == extracted_file
            assert res.filepath.exists()


# --- CLI bracket escaping and output ---


def test_audio_brackets_preserved_in_cli_output(tmp_path):
    """Assert audio title and path containing brackets are printed literally without being swallowed."""
    runner = CliRunner()
    target_file = tmp_path / "Song [Official Audio] [x7X9w_GIm1s].mp3"
    target_file.touch()

    with patch("vdl.downloader.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = {
                "id": "x7X9w_GIm1s",
                "title": "Song [Official Audio]",
                "requested_downloads": [{"filepath": str(target_file)}],
            }

            result = runner.invoke(
                app,
                [
                    "audio",
                    "https://example.com/watch?v=123",
                    "-f",
                    "mp3",
                    "-o",
                    str(tmp_path),
                ],
            )

            assert result.exit_code == 0
            assert "[Official Audio]" in result.output
            assert "[x7X9w_GIm1s]" in result.output


# --- Error handling ---


def test_audio_unsupported_url_friendly_error():
    """vdl audio on unsupported URL gives a friendly error without traceback."""
    runner = CliRunner()
    with patch("vdl.downloader.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.side_effect = UnsupportedError("https://not-supported.test/audio")

            result = runner.invoke(app, ["audio", "https://not-supported.test/audio"])
            assert result.exit_code != 0
            assert "Unsupported URL" in result.output
            assert "Traceback" not in result.output


def test_audio_invalid_format_cli_error():
    """vdl audio with invalid format (e.g. -f flac) is rejected cleanly by Typer validation."""
    runner = CliRunner()
    result = runner.invoke(app, ["audio", "https://example.com/watch?v=123", "-f", "flac"])
    assert result.exit_code != 0
    assert "Invalid value for '-f'" in result.output or "flac" in result.output


def test_audio_invalid_format_function_error(tmp_path):
    """Calling download_audio with an invalid format string raises FormatNotFoundError."""
    settings = Settings(download_dir=tmp_path)
    with patch("vdl.downloader.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with pytest.raises(FormatNotFoundError):
            download_audio(
                url="https://example.com/watch?v=123",
                settings=settings,
                audio_format="wav",
            )
