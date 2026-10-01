"""Unit tests for V3 formats listing, codec preference, storyboard filtering, and follow-ups."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner
from yt_dlp.utils import UnsupportedError

from vdl.cli import app
from vdl.config import Settings
from vdl.downloader import (
    FormatInfo,
    VideoInfo,
    build_format_selector,
    find_nearest_quality,
    format_duration,
    list_formats,
    parse_format,
)
from vdl.exceptions import FormatNotFoundError

# --- Follow-up tests ---


def test_info_tor_unreachable_fails_closed():
    """vdl info --network tor with unreachable proxy exits with NetworkError and YoutubeDL never instantiated."""
    runner = CliRunner()
    with patch("socket.create_connection", side_effect=OSError("Connection refused")):
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            result = runner.invoke(
                app,
                ["info", "https://example.com/watch?v=123", "--network", "tor"],
            )
            assert result.exit_code != 0
            assert "unreachable" in result.output.lower() or "fail closed" in result.output.lower()
            mock_ydl.assert_not_called()


def test_formats_tor_unreachable_fails_closed():
    """vdl formats --network tor with unreachable proxy exits with NetworkError and YoutubeDL never instantiated."""
    runner = CliRunner()
    with patch("socket.create_connection", side_effect=OSError("Connection refused")):
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            result = runner.invoke(
                app,
                ["formats", "https://example.com/watch?v=123", "--network", "tor"],
            )
            assert result.exit_code != 0
            assert "unreachable" in result.output.lower() or "fail closed" in result.output.lower()
            mock_ydl.assert_not_called()


def test_download_prefer_invalid_fails():
    """Invalid --prefer value produces a clean CLI error from Typer Enum validation."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["download", "https://example.com/watch?v=123", "--prefer", "invalid_codec"],
    )
    assert result.exit_code != 0
    assert "Invalid value for '--prefer'" in result.output or "invalid_codec" in result.output


def test_prefer_h264_selector_codec_prefix():
    """Show the format selector produced for --prefer h264 uses a prefix regex matching avc1 codecs."""
    sel = build_format_selector(quality="1080p", prefer_codec="h264")
    # The selector must contain a regex matching prefix like ^(avc|h264)
    assert "vcodec~='^(avc|h264)'" in sel
    # Verify the regex itself matches actual codec strings from extractors (e.g. avc1.640028, avc1.4d401e, h264)
    import re

    pattern = re.compile(r"^(avc|h264)")
    assert pattern.match("avc1.640028")
    assert pattern.match("avc1.4d401e")
    assert pattern.match("h264")
    assert not pattern.match("vp9")
    assert not pattern.match("av01.0.08M")


def test_filesize_display_approximate():
    """filesize_display shows ~ prefix when size comes from filesize_approx."""
    info = VideoInfo(
        id="test",
        title="Test",
        uploader="unknown",
        duration=None,
        duration_string="unknown",
        thumbnail=None,
        resolutions=[],
        filesize=None,
        filesize_approx=33437546,
    )
    assert info.filesize_display.startswith("~")
    assert "31.89 MiB" in info.filesize_display


def test_filesize_display_exact():
    """filesize_display shows exact size without ~ when filesize is set."""
    info = VideoInfo(
        id="test",
        title="Test",
        uploader="unknown",
        duration=None,
        duration_string="unknown",
        thumbnail=None,
        resolutions=[],
        filesize=33437546,
        filesize_approx=None,
    )
    assert not info.filesize_display.startswith("~")
    assert "31.89 MiB" in info.filesize_display


def test_filesize_display_unknown():
    """filesize_display shows 'unknown' when both are None."""
    info = VideoInfo(
        id="test",
        title="Test",
        uploader="unknown",
        duration=None,
        duration_string="unknown",
        thumbnail=None,
        resolutions=[],
        filesize=None,
        filesize_approx=None,
    )
    assert info.filesize_display == "unknown"


def test_format_duration_over_one_hour():
    """format_duration handles videos over an hour (3723s -> 01:02:03)."""
    assert format_duration(3723) == "01:02:03"


# --- FormatInfo properties ---


def test_format_info_video_only():
    f = FormatInfo(
        format_id="137",
        ext="mp4",
        resolution="1080p",
        vcodec="avc1",
        acodec=None,
        is_video=True,
        is_audio=False,
    )
    assert f.is_video_only is True
    assert f.is_audio_only is False
    assert f.is_combined is False
    assert f.type_label == "video only"


def test_format_info_audio_only():
    f = FormatInfo(
        format_id="140",
        ext="m4a",
        resolution="audio only",
        vcodec=None,
        acodec="mp4a",
        is_video=False,
        is_audio=True,
    )
    assert f.is_audio_only is True
    assert f.is_video_only is False
    assert f.is_combined is False
    assert f.type_label == "audio only"


def test_format_info_combined():
    f = FormatInfo(
        format_id="18",
        ext="mp4",
        resolution="360p",
        vcodec="avc1",
        acodec="mp4a",
        is_video=True,
        is_audio=True,
    )
    assert f.is_combined is True
    assert f.is_video_only is False
    assert f.is_audio_only is False
    assert f.type_label == "combined"


def test_format_info_storyboard():
    f = FormatInfo(format_id="sb0", ext="mhtml", resolution="48x27", is_video=False, is_audio=False)
    assert f.is_storyboard is True
    assert f.type_label == "storyboard"


def test_format_info_size_display_exact():
    f = FormatInfo(format_id="22", ext="mp4", resolution="720p", filesize=10485760)
    assert f.size_display == "10.00 MiB"
    assert not f.size_display.startswith("~")


def test_format_info_size_display_approx():
    f = FormatInfo(format_id="22", ext="mp4", resolution="720p", filesize_approx=10485760)
    assert f.size_display == "~10.00 MiB"


def test_format_info_size_display_unknown():
    f = FormatInfo(format_id="22", ext="mp4", resolution="720p")
    assert f.size_display == "unknown"


# --- parse_format ---


def _make_raw_format(**overrides):
    base = {
        "format_id": "22",
        "ext": "mp4",
        "resolution": "1280x720",
        "height": 720,
        "fps": 30,
        "vcodec": "avc1.64001F",
        "acodec": "mp4a.40.2",
        "filesize": 5242880,
        "filesize_approx": None,
        "tbr": 800.0,
        "format_note": "720p",
    }
    base.update(overrides)
    return base


def test_parse_format_combined():
    raw = _make_raw_format()
    f = parse_format(raw)
    assert f.format_id == "22"
    assert f.ext == "mp4"
    assert f.height == 720
    assert f.is_video is True
    assert f.is_audio is True
    assert f.is_combined is True


def test_parse_format_video_only():
    raw = _make_raw_format(format_id="137", acodec="none", height=1080)
    f = parse_format(raw)
    assert f.is_video_only is True
    assert f.height == 1080


def test_parse_format_audio_only():
    raw = _make_raw_format(format_id="140", vcodec="none", height=None, resolution=None)
    f = parse_format(raw)
    assert f.is_audio_only is True
    assert f.resolution == "audio only"
    assert f.height is None


def test_parse_format_storyboard():
    raw = _make_raw_format(
        format_id="sb0", ext="mhtml", vcodec="none", acodec="none", height=27, resolution="48x27"
    )
    f = parse_format(raw)
    assert f.is_storyboard is True


# --- Storyboard filtering ---


SAMPLE_FORMATS = [
    {
        "format_id": "sb0",
        "ext": "mhtml",
        "vcodec": "none",
        "acodec": "none",
        "height": 27,
        "resolution": "48x27",
    },
    {
        "format_id": "sb1",
        "ext": "mhtml",
        "vcodec": "none",
        "acodec": "none",
        "height": 45,
        "resolution": "80x45",
    },
    {
        "format_id": "sb2",
        "ext": "mhtml",
        "vcodec": "none",
        "acodec": "none",
        "height": 90,
        "resolution": "160x90",
    },
    {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "height": None},
    {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "height": None},
    {
        "format_id": "18",
        "ext": "mp4",
        "vcodec": "avc1.42001E",
        "acodec": "mp4a.40.2",
        "height": 360,
    },
    {
        "format_id": "22",
        "ext": "mp4",
        "vcodec": "avc1.64001F",
        "acodec": "mp4a.40.2",
        "height": 720,
    },
    {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080},
    {"format_id": "248", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 1080},
    {"format_id": "313", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 2160},
]


def test_storyboard_filtering_in_list_formats():
    """list_formats excludes storyboards by default."""
    settings = Settings()
    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "id": "test",
            "title": "Test",
            "formats": SAMPLE_FORMATS,
        }

        formats = list_formats("https://example.com/video", settings)
        # No mhtml / storyboard formats
        assert all(f.ext != "mhtml" for f in formats)
        assert all(not f.is_storyboard for f in formats)
        # But video and audio formats are present
        format_ids = {f.format_id for f in formats}
        assert "22" in format_ids
        assert "137" in format_ids
        assert "140" in format_ids


def test_storyboard_included_with_flag():
    """list_formats includes storyboards with include_storyboards=True."""
    settings = Settings()
    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "id": "test",
            "title": "Test",
            "formats": SAMPLE_FORMATS,
        }

        formats = list_formats("https://example.com/video", settings, include_storyboards=True)
        storyboards = [f for f in formats if f.is_storyboard]
        assert len(storyboards) >= 3  # sb0, sb1, sb2


def test_storyboards_excluded_from_info_resolutions():
    """fetch_info resolutions should not include storyboard heights (27p, 45p, 90p)."""
    settings = Settings()
    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "id": "test",
            "title": "Test",
            "formats": SAMPLE_FORMATS,
        }

        from vdl.downloader import fetch_info

        info = fetch_info("https://example.com/video", settings)
        # No storyboard resolutions
        assert "27p" not in info.resolutions
        assert "45p" not in info.resolutions
        assert "90p" not in info.resolutions
        # Real resolutions are present
        assert "2160p" in info.resolutions
        assert "1080p" in info.resolutions
        assert "720p" in info.resolutions
        assert "360p" in info.resolutions


# --- build_format_selector with codec preference ---


def test_build_format_selector_codec_h264_best():
    sel = build_format_selector(quality="best", prefer_codec="h264")
    assert "vcodec~=" in sel
    assert "avc" in sel or "h264" in sel
    # Falls back to non-codec-filtered
    assert "/bv*+ba/b" in sel


def test_build_format_selector_codec_av1_720p():
    sel = build_format_selector(quality="720p", prefer_codec="av1")
    assert "av0?1" in sel or "av1" in sel
    # Falls back to unfiltered
    assert "/bv*[height<=720]+ba" in sel
    assert "/b[height<=720]/b" in sel


def test_build_format_selector_codec_any_default():
    """Default (any) produces the standard selector without codec filter."""
    sel = build_format_selector(quality="best", prefer_codec="any")
    assert sel == "bv*+ba/b"


def test_build_format_selector_codec_none_default():
    """None prefer_codec produces the standard selector."""
    sel = build_format_selector(quality="1080p", prefer_codec=None)
    assert sel == "bv*[height<=1080]+ba/b[height<=1080]/b"


# --- find_nearest_quality ---


def _make_format_with_height(h):
    return FormatInfo(
        format_id=str(h),
        ext="mp4",
        resolution=f"{h}p",
        height=h,
        is_video=True,
        is_audio=False,
    )


def test_find_nearest_quality_exact_match():
    fmts = [_make_format_with_height(h) for h in [2160, 1080, 720, 480, 360]]
    qual, msg = find_nearest_quality("720p", fmts)
    assert qual == "720p"
    assert msg is None


def test_find_nearest_quality_fallback_lower():
    fmts = [_make_format_with_height(h) for h in [2160, 1080, 720, 360]]
    qual, msg = find_nearest_quality("480p", fmts)
    assert qual == "360p"
    assert msg is not None
    assert "360p" in msg


def test_find_nearest_quality_fallback_best():
    """When all formats are above target, fall back to best."""
    fmts = [_make_format_with_height(h) for h in [2160, 1080, 720]]
    qual, msg = find_nearest_quality("480p", fmts)
    assert qual == "best"
    assert msg is not None


def test_find_nearest_quality_best_passthrough():
    fmts = [_make_format_with_height(h) for h in [1080, 720]]
    qual, msg = find_nearest_quality("best", fmts)
    assert qual == "best"
    assert msg is None


# --- FormatNotFoundError for missing format_id ---


def test_missing_format_id_raises_format_not_found():
    with pytest.raises(FormatNotFoundError):
        build_format_selector(quality="abc")


# --- CLI formats command ---


def test_formats_cli_table_output():
    """vdl formats renders a Rich table with correct structure."""
    runner = CliRunner()
    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "id": "test",
            "title": "Test Video [Brackets]",
            "formats": SAMPLE_FORMATS,
        }

        result = runner.invoke(app, ["formats", "https://example.com/video"])
        assert result.exit_code == 0
        # Table headers (may be truncated by Rich in narrow terminal)
        assert "ID" in result.output
        assert "Ext" in result.output
        assert "FPS" in result.output
        # Content rows: format ids from non-storyboard formats
        assert "22" in result.output
        assert "137" in result.output
        assert "140" in result.output
        # No storyboards by default
        assert "sb0" not in result.output


def test_formats_cli_all_includes_storyboards():
    """vdl formats --all includes storyboard formats."""
    runner = CliRunner()
    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "id": "test",
            "title": "Test",
            "formats": SAMPLE_FORMATS,
        }

        result = runner.invoke(app, ["formats", "https://example.com/video", "--all"])
        assert result.exit_code == 0
        assert "sb0" in result.output


def test_formats_note_with_brackets_rendered_literally():
    """Format note containing brackets renders literally in Rich table."""
    runner = CliRunner()
    fmt = {
        "format_id": "22",
        "ext": "mp4",
        "vcodec": "avc1",
        "acodec": "mp4a",
        "height": 720,
        "format_note": "720p [HQ Version]",
    }
    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "id": "test",
            "title": "Test",
            "formats": [fmt],
        }

        result = runner.invoke(app, ["formats", "https://example.com/video"])
        assert result.exit_code == 0
        # Rich may wrap long text across lines, so check parts separately
        # The key point is brackets are not swallowed by Rich markup
        assert "HQ" in result.output
        assert "Version" in result.output


def test_formats_unsupported_url():
    """vdl formats on unsupported URL gives friendly error."""
    runner = CliRunner()
    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = UnsupportedError("https://bad.test/foo")

        result = runner.invoke(app, ["formats", "https://bad.test/foo"])
        assert result.exit_code != 0
        assert "Unsupported URL" in result.output
