"""Unit tests for V7 network unification, Tor reachability, fail-closed behavior, and proxy resolution."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from vdl.cli import app
from vdl.config import Settings
from vdl.downloader import build_ydl_opts
from vdl.exceptions import NetworkError
from vdl.network import (
    parse_proxy_host_port,
    setup_network,
)


def test_parse_proxy_host_port():
    """Verify proxy URL parsing extracts host and port with correct defaults."""
    host, port = parse_proxy_host_port("socks5://127.0.0.1:9050")
    assert host == "127.0.0.1"
    assert port == 9050

    host, port = parse_proxy_host_port("socks5h://localhost:9150")
    assert host == "localhost"
    assert port == 9150


def test_setup_network_direct_mode(capsys):
    """setup_network in direct mode returns None, does not probe socket, and does not emit Tor notice."""
    settings = Settings(network_mode="direct", tor_proxy="socks5h://127.0.0.1:9050")
    with patch("vdl.network.socket.create_connection") as mock_conn:
        proxy = setup_network(settings)
        assert proxy is None
        assert settings.proxy is None
        mock_conn.assert_not_called()

    captured = capsys.readouterr()
    assert "direct" in captured.err.lower()
    assert "anonymity" not in captured.err.lower()


def test_settings_proxy_none_in_direct_mode_defense_in_depth():
    """Settings.proxy property must return None when network_mode is direct, even if tor_proxy is set."""
    settings = Settings(network_mode="direct", tor_proxy="socks5h://127.0.0.1:9999")
    assert settings.proxy is None
    # Contrast with tor mode
    settings.network_mode = "tor"
    assert settings.proxy == "socks5h://127.0.0.1:9999"


def test_setup_network_tor_reachable(capsys):
    """setup_network in tor mode with reachable socket returns proxy URL and prints notice."""
    settings = Settings(network_mode="tor", tor_proxy="socks5h://127.0.0.1:9050")
    with patch("vdl.network.socket.create_connection"):
        proxy = setup_network(settings)
        assert proxy == "socks5h://127.0.0.1:9050"
        assert settings.proxy == "socks5h://127.0.0.1:9050"

    captured = capsys.readouterr()
    assert "tor" in captured.err.lower()
    assert "reduces ip exposure" in captured.err.lower()


def test_setup_network_tor_unreachable_fails_closed():
    """setup_network in tor mode with unreachable socket raises NetworkError."""
    settings = Settings(network_mode="tor", tor_proxy="socks5h://127.0.0.1:9050")
    with patch("vdl.network.socket.create_connection", side_effect=OSError("Connection refused")):
        with pytest.raises(NetworkError) as exc_info:
            setup_network(settings)
        assert "unreachable" in exc_info.value.message.lower()
        assert "fail closed" in exc_info.value.message.lower()


def test_setup_network_cli_override_precedence():
    """CLI --network override takes precedence over config settings for that invocation."""
    # 1. Config is direct, CLI overrides to tor
    settings_dir = Settings(network_mode="direct")
    with patch("vdl.network.socket.create_connection"):
        proxy = setup_network(settings_dir, network_override="tor")
        assert proxy == settings_dir.tor_proxy
        assert settings_dir.network_mode == "tor"

    # 2. Config is tor, CLI overrides to direct
    settings_tor = Settings(network_mode="tor")
    proxy_direct = setup_network(settings_tor, network_override="direct")
    assert proxy_direct is None
    assert settings_tor.network_mode == "direct"


@pytest.mark.parametrize(
    "cmd_args",
    [
        ["download", "https://example.com/video"],
        ["info", "https://example.com/video"],
        ["formats", "https://example.com/video"],
        ["audio", "https://example.com/video"],
    ],
)
def test_fail_closed_uniformly_across_all_commands(cmd_args):
    """Verify all network commands uniformly fail closed without instantiating YoutubeDL when Tor proxy is unreachable."""
    runner = CliRunner()
    with patch("socket.create_connection", side_effect=OSError("Connection refused")):
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            result = runner.invoke(app, [*cmd_args, "--network", "tor"])
            assert result.exit_code != 0
            assert "unreachable" in result.output.lower() or "fail closed" in result.output.lower()
            mock_ydl.assert_not_called()


def test_tor_notice_printed_exactly_once(tmp_path):
    """Verify Tor privacy notice is printed exactly once per invocation, not repeatedly."""
    runner = CliRunner()
    dummy_file = tmp_path / "video.mp4"
    dummy_file.touch()

    with patch("vdl.media.shutil.which", return_value="C:\\ffmpeg.exe"):
        with patch("socket.create_connection"):
            with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
                mock_ydl = MagicMock()
                mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
                mock_ydl.extract_info.return_value = {
                    "id": "123",
                    "title": "Video",
                    "requested_downloads": [{"filepath": str(dummy_file)}],
                }

                result = runner.invoke(
                    app, ["info", "https://example.com/video", "--network", "tor"]
                )
                assert result.exit_code == 0
                # Notice phrase appears exactly once
                assert result.output.count("reduces IP exposure") == 1


def test_rate_limit_wired_into_build_ydl_opts(tmp_path):
    """Verify Settings.rate_limit is wired directly into yt-dlp 'ratelimit' option in bytes per second."""
    settings = Settings(download_dir=tmp_path, rate_limit=1048576)
    opts = build_ydl_opts(settings)
    assert opts["ratelimit"] == 1048576


def test_tor_mode_no_credentials_or_cookies_passed(tmp_path):
    """Verify no browser cookies, cookie files, or user credentials are leaked/passed in Tor mode."""
    settings = Settings(
        download_dir=tmp_path,
        network_mode="tor",
        tor_proxy="socks5h://127.0.0.1:9050",
    )
    opts = build_ydl_opts(settings)
    assert opts["proxy"] == "socks5h://127.0.0.1:9050"
    assert opts.get("cookiesfrombrowser") is None
    assert "cookiefile" not in opts or opts.get("cookiefile") is None
    assert "username" not in opts
    assert "password" not in opts
