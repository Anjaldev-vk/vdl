from unittest.mock import MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError as YtDlpDownloadError
from yt_dlp.utils import UnsupportedError

from vdl.config import Settings
from vdl.downloader import (
    DownloadResult,
    build_format_selector,
    build_ydl_opts,
    download_video,
    format_duration,
    format_size,
)
from vdl.exceptions import (
    DownloadError,
    FFmpegNotFoundError,
    FormatNotFoundError,
    UnsupportedUrlError,
)


def test_format_duration():
    assert format_duration(None) == "unknown"
    assert format_duration(-5) == "unknown"
    assert format_duration(45) == "00:45"
    assert format_duration(3665) == "01:01:05"


def test_format_size():
    assert format_size(None) == "unknown"
    assert format_size(0) == "unknown"
    assert format_size(500) == "500.00 B"
    assert format_size(1024 * 1024 * 15.5) == "15.50 MiB"


def test_build_format_selector_valid():
    # best with merge
    assert build_format_selector(quality="best", allow_merge=True) == "bv*+ba/b"
    # best without merge
    assert build_format_selector(quality="best", allow_merge=False) == "b"
    # height with merge
    assert (
        build_format_selector(quality="720p", allow_merge=True)
        == "bv*[height<=720]+ba/b[height<=720]/b"
    )
    # height without merge
    assert build_format_selector(quality="1080p", allow_merge=False) == "b[height<=1080]/b"
    # custom format_id
    assert build_format_selector(format_id="22") == "22"
    # format_id video-only
    assert build_format_selector(format_id="137", is_video_only=True) == "137+ba/b"


def test_build_format_selector_invalid():
    with pytest.raises(FormatNotFoundError):
        build_format_selector(quality="invalid_quality")


def test_download_video_success(tmp_path):
    settings = Settings(download_dir=tmp_path)
    fake_info = {"id": "test1234", "title": "Test Title"}
    target_file = tmp_path / "Test Title [test1234].mp4"
    target_file.touch()

    with patch("vdl.downloader.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = fake_info
            mock_ydl.prepare_filename.return_value = str(target_file)

            res = download_video(
                url="https://example.com/watch?v=123",
                settings=settings,
                quality="720p",
            )

            assert isinstance(res, DownloadResult)
            assert res.title == "Test Title"
            assert res.filepath == target_file
            # Verify quiet=True, noprogress=True
            called_opts = mock_ydl_cls.call_args[0][0]
            assert called_opts["quiet"] is True
            assert called_opts["noprogress"] is True
            assert called_opts["format"] == "bv*[height<=720]+ba/b[height<=720]/b"


def test_download_video_missing_ffmpeg_raises(tmp_path):
    settings = Settings(download_dir=tmp_path)
    # When FFmpeg is missing, requesting video-only format or merge should raise or fallback
    with patch("vdl.media.shutil.which", return_value=None):
        with pytest.raises(FFmpegNotFoundError):
            download_video(
                url="https://example.com/watch?v=123",
                settings=settings,
                format_id="137+140",  # Explicit merge format
            )


def test_download_video_unsupported_url(tmp_path):
    settings = Settings(download_dir=tmp_path)
    with patch("vdl.downloader.get_ffmpeg_path", return_value=None):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.side_effect = UnsupportedError("https://unknown.site/foo")

            with pytest.raises(UnsupportedUrlError):
                download_video(
                    url="https://unknown.site/foo",
                    settings=settings,
                )


def test_strip_ansi():
    from vdl.downloader import strip_ansi

    raw = "\x1b[0;31mERROR:\x1b[0m [youtube] \x1b[1;32mTest Message\x1b[0m"
    assert strip_ansi(raw) == "ERROR: [youtube] Test Message"


def test_logger_bridge_ansi_and_prefix():
    from vdl.downloader import YtDlpLoggerBridge

    bridge = YtDlpLoggerBridge()
    bridge.error("\x1b[31mERROR: \x1b[0mSomething failed")
    assert bridge.captured_errors == ["Something failed"]

    bridge.warning("\x1b[33mWARNING: \x1b[0mNo supported JavaScript runtime could be found")
    assert bridge.seen_js_runtime_warning is True
    assert bridge.captured_warnings == ["No supported JavaScript runtime could be found"]


def test_translate_ytdlp_error_deno_hint():
    from vdl.downloader import YtDlpLoggerBridge, _translate_ytdlp_error

    bridge = YtDlpLoggerBridge()
    bridge.seen_js_runtime_warning = True

    exc = YtDlpDownloadError(
        "ERROR: [youtube] abc123: Requested format is not available. Use --list-formats for a list of available formats"
    )
    err = _translate_ytdlp_error(exc, "https://youtube.com/watch?v=abc123", logger_bridge=bridge)

    assert isinstance(err, DownloadError)
    assert not err.message.startswith("ERROR:")
    assert "Requested format is not available" in err.message
    assert "JavaScript runtime" in err.message
    assert "Deno" in err.message


def test_translate_ytdlp_error_no_deno_hint_when_no_js_warning():
    from vdl.downloader import YtDlpLoggerBridge, _translate_ytdlp_error

    bridge = YtDlpLoggerBridge()
    bridge.seen_js_runtime_warning = False

    exc = YtDlpDownloadError("ERROR: [youtube] abc123: Requested format is not available")
    err = _translate_ytdlp_error(exc, "https://youtube.com/watch?v=abc123", logger_bridge=bridge)

    assert isinstance(err, DownloadError)
    assert "Requested format is not available" in err.message
    assert "Deno" not in err.message


def test_download_resolves_merged_filepath_over_hooks(tmp_path):
    """Assert DownloadResult.filepath is the merged file from requested_downloads, not intermediate hooks."""
    settings = Settings(download_dir=tmp_path)
    merged_file = tmp_path / "Video [123].mkv"
    merged_file.touch()

    # yt-dlp requested_downloads carries the post-merge file
    fake_info = {
        "id": "123",
        "title": "Video",
        "requested_downloads": [{"filepath": str(merged_file)}],
    }

    with patch("vdl.downloader.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

            def fake_extract_info(url, download=True):
                # Simulate intermediate hook firing during download
                return fake_info

            mock_ydl.extract_info.side_effect = fake_extract_info
            mock_ydl.prepare_filename.return_value = str(tmp_path / "Video [123].mp4")

            res = download_video(
                url="https://example.com/watch?v=123",
                settings=settings,
                quality="best",
            )

            assert res.filepath == merged_file
            assert res.filepath.exists()


def test_tor_mode_unreachable_fails_closed_without_instantiating_ytdlp(tmp_path):
    """In tor mode with unreachable proxy, NetworkError is raised and YoutubeDL is never instantiated."""
    from typer.testing import CliRunner

    from vdl.cli import app

    runner = CliRunner()
    with patch("socket.create_connection", side_effect=OSError("Connection refused")):
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            result = runner.invoke(
                app,
                [
                    "download",
                    "https://example.com/watch?v=123",
                    "--network",
                    "tor",
                    "-o",
                    str(tmp_path),
                ],
            )
            # Must fail and not be 0
            assert result.exit_code != 0
            assert "unreachable" in result.output.lower() or "fail closed" in result.output.lower()
            # YoutubeDL must never be instantiated
            mock_ydl.assert_not_called()


def test_missing_ffmpeg_emits_visible_warning(tmp_path, capsys):
    """A visible warning is emitted when FFmpeg is missing and single-stream fallback is used."""
    settings = Settings(download_dir=tmp_path)
    dummy_file = tmp_path / "Video [123].mp4"
    dummy_file.touch()

    fake_info = {
        "id": "123",
        "title": "Video",
        "requested_downloads": [{"filepath": str(dummy_file)}],
    }

    with patch("vdl.media.shutil.which", return_value=None):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = fake_info

            download_video(
                url="https://example.com/watch?v=123",
                settings=settings,
                quality="best",
            )

            captured = capsys.readouterr()
            # Warning must appear on stderr
            assert (
                "FFmpeg not detected; falling back to pre-merged single file format."
                in captured.err
            )


def test_ytdlp_options_never_contain_credentials_or_cookies(tmp_path):
    """Ensure yt-dlp options never contain cookie files, browser cookies, or credentials."""
    from vdl.downloader import build_ydl_opts

    settings = Settings(download_dir=tmp_path)
    opts = build_ydl_opts(settings)

    assert "cookiefile" not in opts
    assert opts.get("cookiesfrombrowser") is None
    assert "username" not in opts
    assert "password" not in opts
    assert "netrc" not in opts
    assert "netrc_location" not in opts
    assert "video_password" not in opts


def test_ytdlp_options_never_contain_credentials_or_cookies_in_tor_mode(tmp_path):
    """Ensure yt-dlp options never contain credential or cookie fields when tor mode is active."""
    settings = Settings(
        download_dir=tmp_path, network_mode="tor", tor_proxy="socks5h://127.0.0.1:9050"
    )
    opts = build_ydl_opts(settings)

    assert opts["proxy"] == "socks5h://127.0.0.1:9050"
    assert "cookiefile" not in opts
    assert opts.get("cookiesfrombrowser") is None
    assert "username" not in opts
    assert "password" not in opts
    assert "netrc" not in opts
    assert "netrc_location" not in opts
    assert "video_password" not in opts


@pytest.mark.integration
def test_opt_in_live_download(tmp_path):
    """Opt-in live network test (skipped by default)."""
    settings = Settings(download_dir=tmp_path)
    # Small public domain sample video
    url = "https://www.w3schools.com/html/mov_bbb.mp4"
    result = download_video(url=url, settings=settings)
    assert result.title
    assert result.filepath.exists()
