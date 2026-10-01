"""Unit tests for V8 CLI polish: help text, flag consistency, exit codes, and version handling."""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from vdl import __version__
from vdl.cli import app
from vdl.exceptions import UnsupportedUrlError


@pytest.fixture
def runner():
    return CliRunner()


def test_top_level_help_shows_legal_notice_and_all_commands(runner):
    """Top-level vdl --help must display the legal notice prominently and list all subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.output

    # Legal notice check
    assert "LEGAL NOTICE" in output
    assert "You must only download content you are authorized to download" in output

    # All registered commands listed
    for cmd in ["download", "info", "formats", "audio", "config", "doctor", "version"]:
        assert cmd in output

    # Root options listed
    assert "--version" in output or "-V" in output
    assert "--verbose" in output or "-v" in output
    assert "--debug" in output


@pytest.mark.parametrize(
    ("cmd", "expected_snippets"),
    [
        (
            "download",
            [
                "Download a video or playlist",
                "Examples:",
                "-o",
                "--output",
                "-q",
                "--quality",
                "--format-id",
                "--network",
                "--playlist",
                "--prefer",
            ],
        ),
        (
            "info",
            [
                "Fetch and display metadata",
                "Example:",
                "--network",
            ],
        ),
        (
            "formats",
            [
                "List all available formats",
                "Example:",
                "--network",
                "--all",
            ],
        ),
        (
            "audio",
            [
                "Download and extract audio",
                "Examples:",
                "-f",
                "--format",
                "-o",
                "--output",
                "--network",
            ],
        ),
        (
            "doctor",
            [
                "Diagnose system environment",
                "Example:",
            ],
        ),
        (
            "version",
            [
                "Display installed vdl version",
                "Example:",
            ],
        ),
    ],
)
def test_subcommand_help_contains_description_options_and_examples(runner, cmd, expected_snippets):
    """Every subcommand --help must include what it does, option explanations, and example usage."""
    result = runner.invoke(app, [cmd, "--help"])
    assert result.exit_code == 0
    for snippet in expected_snippets:
        assert snippet in result.output, f"Missing '{snippet}' in '{cmd} --help' output"


def test_config_subcommands_help(runner):
    """vdl config --help must list show, set, reset, and path subcommands."""
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    for sub in ["show", "set", "reset", "path"]:
        assert sub in result.output


def test_version_flag_and_subcommand(runner):
    """Verify vdl --version, vdl -V, and vdl version all report the current version consistently."""
    # 1. Flag --version
    res1 = runner.invoke(app, ["--version"])
    assert res1.exit_code == 0
    assert f"vdl version {__version__}" in res1.output

    # 2. Flag -V
    res2 = runner.invoke(app, ["-V"])
    assert res2.exit_code == 0
    assert f"vdl version {__version__}" in res2.output

    # 3. Subcommand version
    res3 = runner.invoke(app, ["version"])
    assert res3.exit_code == 0
    assert f"vdl version {__version__}" in res3.output


def test_doctor_stub_command(runner):
    """Verify vdl doctor stub executes with code 0."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "doctor" in result.output.lower()


@pytest.mark.parametrize(
    "cmd_args",
    [
        ["download", "https://example.com/video"],
        ["info", "https://example.com/video"],
        ["formats", "https://example.com/video"],
        ["audio", "https://example.com/video"],
    ],
)
def test_consistent_cancellation_exit_code_130(runner, cmd_args):
    """All interactive commands exit with 130 on KeyboardInterrupt."""
    with patch("vdl.cli.setup_network"):
        with patch("vdl.cli.download_video", side_effect=KeyboardInterrupt):
            with patch("vdl.cli.fetch_info", side_effect=KeyboardInterrupt):
                with patch("vdl.cli.list_formats", side_effect=KeyboardInterrupt):
                    with patch("vdl.cli.download_audio", side_effect=KeyboardInterrupt):
                        result = runner.invoke(app, cmd_args)
                        assert result.exit_code == 130
                        assert "cancelled by user" in result.output.lower()


@pytest.mark.parametrize(
    "cmd_args",
    [
        ["download", "https://example.com/video"],
        ["info", "https://example.com/video"],
        ["formats", "https://example.com/video"],
        ["audio", "https://example.com/video"],
    ],
)
def test_consistent_general_error_exit_code_1(runner, cmd_args):
    """All commands exit with exit code 1 and red Error: prefix on general exceptions."""
    with patch("vdl.cli.setup_network"):
        with patch("vdl.cli.download_video", side_effect=UnsupportedUrlError("bad url")):
            with patch("vdl.cli.fetch_info", side_effect=UnsupportedUrlError("bad url")):
                with patch("vdl.cli.list_formats", side_effect=UnsupportedUrlError("bad url")):
                    with patch(
                        "vdl.cli.download_audio", side_effect=UnsupportedUrlError("bad url")
                    ):
                        result = runner.invoke(app, cmd_args)
                        assert result.exit_code == 1
                        assert "Error:" in result.output


def test_flag_consistency_across_commands():
    """Verify consistent flag naming style across commands via Click/Typer command objects."""
    import typer.main

    click_app = typer.main.get_command(app)
    commands = click_app.commands

    # 1. All network-touching commands must define --network
    network_commands = ["download", "info", "formats", "audio"]
    for cmd_name in network_commands:
        cmd = commands[cmd_name]
        param_names = [p.name for p in cmd.params]
        assert "network" in param_names, f"Command '{cmd_name}' missing 'network' parameter"
        opts = [opt for p in cmd.params for opt in p.opts]
        assert "--network" in opts, f"Command '{cmd_name}' missing '--network' option flag"

    # 2. Output directory commands must define -o and --output / --output-dir
    output_commands = ["download", "audio"]
    for cmd_name in output_commands:
        cmd = commands[cmd_name]
        opts = [opt for p in cmd.params for opt in p.opts]
        assert "-o" in opts, f"Command '{cmd_name}' missing '-o' short flag"
        assert "--output" in opts or "--output-dir" in opts, (
            f"Command '{cmd_name}' missing output flag"
        )

    # 3. Audio format must support -f and --format
    audio_cmd = commands["audio"]
    audio_opts = [opt for p in audio_cmd.params for opt in p.opts]
    assert "-f" in audio_opts
    assert "--format" in audio_opts

    # 4. Download quality must support -q and --quality
    dl_cmd = commands["download"]
    dl_opts = [opt for p in dl_cmd.params for opt in p.opts]
    assert "-q" in dl_opts
    assert "--quality" in dl_opts
    assert "--format-id" in dl_opts
    assert "-f" not in dl_opts  # -f is reserved exclusively for audio --format

    # 5. Semantic consistency: assert -f short flag is defined exclusively on 'audio' for '--format'
    f_flag_commands = []
    for cmd_name, cmd in commands.items():
        for p in cmd.params:
            if "-f" in p.opts:
                f_flag_commands.append((cmd_name, p.name))
    assert f_flag_commands == [("audio", "format")], (
        f"Expected -f only on audio format, found: {f_flag_commands}"
    )
