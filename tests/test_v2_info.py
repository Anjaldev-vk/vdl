"""Unit tests for V2 info extraction and Rich display escaping."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner
from yt_dlp.utils import UnsupportedError

from vdl.cli import app
from vdl.config import Settings
from vdl.display import render_info
from vdl.downloader import VideoInfo, fetch_info


def test_clirunner_download_brackets_preserved(tmp_path):
    """Assert a downloaded path and title with brackets are printed literally and not swallowed by Rich markup."""
    runner = CliRunner()
    fake_title = "Python in 100 Seconds [x7X9w_GIm1s] [Official Video]"
    dummy_file = tmp_path / "Python in 100 Seconds [x7X9w_GIm1s].webm"
    dummy_file.touch()

    fake_info = {
        "id": "x7X9w_GIm1s",
        "title": fake_title,
        "requested_downloads": [{"filepath": str(dummy_file)}],
    }

    with patch("vdl.downloader.get_ffmpeg_path", return_value="C:\\ffmpeg.exe"):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = fake_info

            result = runner.invoke(
                app,
                [
                    "download",
                    "https://youtu.be/x7X9w_GIm1s",
                    "-o",
                    str(tmp_path),
                ],
            )

            assert result.exit_code == 0
            # Both bracketed title and bracketed filename must appear verbatim in the output
            assert "[x7X9w_GIm1s]" in result.output
            assert "[Official Video]" in result.output
            assert "Python in 100 Seconds [x7X9w_GIm1s].webm" in result.output


def test_fetch_info_success():
    """Mock YoutubeDL for info extraction returning a VideoInfo dataclass."""
    settings = Settings()
    fake_raw = {
        "id": "vid123",
        "title": "Sample Title [Test]",
        "uploader": "Sample Creator",
        "duration": 125,
        "thumbnail": "https://img.example.com/thumb.jpg",
        "filesize": 10485760,
        "formats": [
            {"format_id": "18", "height": 360, "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"},
            {"format_id": "22", "height": 720, "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"},
            {"format_id": "137", "height": 1080, "ext": "mp4", "vcodec": "avc1", "acodec": "none"},
        ],
    }

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = fake_raw

        info = fetch_info("https://example.com/video", settings=settings)

        assert isinstance(info, VideoInfo)
        assert not isinstance(info, dict)
        assert info.id == "vid123"
        assert info.title == "Sample Title [Test]"
        assert info.uploader == "Sample Creator"
        assert info.duration == 125.0
        assert info.duration_string == "02:05"
        assert info.thumbnail == "https://img.example.com/thumb.jpg"
        assert info.filesize == 10485760
        assert info.filesize_display == "10.00 MiB"
        # Resolutions sorted descending
        assert info.resolutions == ["1080p", "720p", "360p"]

        # Assert download=False and noplaylist=True were passed to YoutubeDL
        called_opts = mock_ydl_cls.call_args[0][0]
        assert called_opts["noplaylist"] is True
        mock_ydl.extract_info.assert_called_once_with("https://example.com/video", download=False)


def test_fetch_info_missing_fields():
    """Test metadata extraction when optional fields (uploader, duration, filesize) are missing."""
    settings = Settings()
    fake_raw = {
        "id": "minimal1",
        "title": "Minimal Video",
        "formats": [],
    }

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = fake_raw

        info = fetch_info("https://example.com/minimal", settings=settings)

        assert info.id == "minimal1"
        assert info.title == "Minimal Video"
        assert info.uploader == "unknown"
        assert info.duration is None
        assert info.duration_string == "unknown"
        assert info.filesize is None
        assert info.filesize_display == "unknown"
        assert info.thumbnail is None
        assert info.resolutions == []


def test_render_info_brackets_literally(capsys):
    """Verify render_info renders bracketed titles and metadata without stripping brackets."""
    info = VideoInfo(
        id="x7X9w_GIm1s",
        title="Python in [100 Seconds] [Official]",
        uploader="Fireship [Verified]",
        duration=143.0,
        duration_string="02:23",
        thumbnail="https://example.com/[thumb].jpg",
        resolutions=["1080p", "720p"],
        filesize=33437546,
    )

    render_info(info)
    captured = capsys.readouterr()

    assert "[100 Seconds]" in captured.out
    assert "[Official]" in captured.out
    assert "Fireship [Verified]" in captured.out
    assert "31.89 MiB" in captured.out
    assert "02:23" in captured.out


def test_vdl_info_cli_unsupported_url():
    """Verify vdl info on an unsupported URL fails gracefully with friendly message and no stack trace."""
    runner = CliRunner()
    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = UnsupportedError(
            "https://not-a-supported-site.test/abc"
        )

        result = runner.invoke(app, ["info", "https://not-a-supported-site.test/abc"])

        assert result.exit_code != 0
        assert "Unsupported URL" in result.output
        assert "Traceback" not in result.output
