"""Command-line interface for vdl."""

from __future__ import annotations

import logging
import sys
from dataclasses import asdict
from enum import StrEnum
from logging.handlers import RotatingFileHandler
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape

from vdl import __version__
from vdl.config import (
    ConfigError,
    get_config_path,
    get_log_dir,
    load_settings,
    reset_settings,
    save_settings,
    set_setting,
)
from vdl.display import (
    RichProgressHandler,
    print_error,
    print_success,
    print_warning,
    render_formats_table,
    render_info,
)
from vdl.doctor import run_doctor
from vdl.downloader import (
    download_audio,
    download_video,
    fetch_info,
    find_nearest_quality,
    list_formats,
)
from vdl.exceptions import VdlError
from vdl.network import setup_network


class CodecPreference(StrEnum):
    """Supported video codec preferences."""

    H264 = "h264"
    AV1 = "av1"
    ANY = "any"


class AudioFormat(StrEnum):
    """Supported audio extraction target formats."""

    MP3 = "mp3"
    M4A = "m4a"
    OPUS = "opus"
    BEST = "best"


app = typer.Typer(
    name="vdl",
    help=(
        "vdl: Production-quality terminal video downloader powered by yt-dlp.\n\n"
        "LEGAL NOTICE: You must only download content you are authorized to download."
    ),
    add_completion=False,
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)
_DEBUG_MODE: bool = False
logger = logging.getLogger("vdl.cli")


def version_callback(value: bool) -> None:
    """Handle top-level -V / --version option."""
    if value:
        console.print(f"vdl version {__version__}")
        raise typer.Exit()


def configure_logging(verbose: bool = False, debug: bool = False) -> None:
    """Configure console logging and rotating file logging writing to the platform log directory."""
    global _DEBUG_MODE
    _DEBUG_MODE = debug

    console_level = logging.WARNING
    if debug:
        console_level = logging.DEBUG
    elif verbose:
        console_level = logging.INFO

    root_logger = logging.getLogger()
    # Root logger captures at least INFO so the file handler receives operational logs
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    root_logger.handlers.clear()

    # 1. Console RichHandler
    console_handler = RichHandler(
        console=console,
        show_time=False,
        show_path=debug,
        markup=False,
    )
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(console_handler)

    # 2. RotatingFileHandler to platform log directory
    try:
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "vdl.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB per file
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
        logger.debug("Configured rotating file logger at %s", log_file)
    except Exception as exc:
        logger.debug("Failed initializing rotating file logger: %s", exc)


@app.callback()
def main(
    version: bool | None = typer.Option(
        None,
        "-V",
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show application version and exit.",
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose log messages."),
    debug: bool = typer.Option(
        False, "--debug", help="Enable debug log messages and full stack traces on error."
    ),
) -> None:
    """vdl: Terminal video downloader."""
    configure_logging(verbose=verbose, debug=debug)


@app.command()
def download(
    url: str = typer.Argument(..., help="Media URL to download."),
    output_dir: Path | None = typer.Option(
        None,
        "-o",
        "--output",
        "--output-dir",
        help="Directory where downloaded media should be saved.",
    ),
    quality: str | None = typer.Option(
        None, "-q", "--quality", help="Desired video quality (e.g. 1080p, 720p, 480p, best)."
    ),
    format_id: str | None = typer.Option(
        None, "--format-id", help="Exact yt-dlp format ID to download."
    ),
    network: str | None = typer.Option(
        None, "--network", help="Network mode: 'direct' or 'tor' (overrides config)."
    ),
    playlist: bool = typer.Option(
        False, "--playlist", help="Download full playlist if URL points to one."
    ),
    prefer: CodecPreference = typer.Option(
        CodecPreference.ANY,
        "--prefer",
        help="Codec preference: h264 (broad compatibility), av1 (high efficiency), or any (default).",
    ),
) -> None:
    """Download a video or playlist from the specified URL.

    Examples:
        vdl download "https://youtu.be/x7X9w_GIm1s"
        vdl download "https://youtu.be/x7X9w_GIm1s" -q 1080p --prefer av1
        vdl download "https://youtu.be/x7X9w_GIm1s" -o ./downloads --network tor
    """
    settings = load_settings()
    if output_dir:
        settings.download_dir = output_dir.expanduser()
    if network:
        settings.network_mode = network

    try:
        setup_network(settings, network_override=network)

        # If quality is specified (not a format_id), try nearest-height fallback
        effective_quality = quality
        if quality and not format_id:
            try:
                formats = list_formats(url, settings)
                effective_quality, fallback_msg = find_nearest_quality(quality, formats)
                if fallback_msg:
                    print_warning(fallback_msg)
            except Exception:
                # If format listing fails, proceed with original quality
                effective_quality = quality

        with RichProgressHandler(console=console) as progress_handler:
            result = download_video(
                url=url,
                settings=settings,
                quality=effective_quality,
                format_id=format_id,
                is_playlist=playlist,
                progress_hooks=[progress_handler],
                postprocessor_hooks=[progress_handler.postprocessor_hook],
                prefer_codec=prefer.value,
            )
        print_success("Successfully downloaded", title=result.title, path=result.filepath)
    except KeyboardInterrupt:
        print_warning("Operation cancelled by user.")
        sys.exit(130)
    except VdlError as exc:
        if _DEBUG_MODE:
            console.print_exception()
        else:
            print_error(exc.message)
        sys.exit(exc.exit_code)
    except Exception as exc:
        if _DEBUG_MODE:
            console.print_exception()
        else:
            print_error(f"Unexpected error: {exc}")
        sys.exit(1)


@app.command()
def info(
    url: str = typer.Argument(..., help="Media URL to inspect."),
    network: str | None = typer.Option(
        None, "--network", help="Network mode: 'direct' or 'tor' (overrides config)."
    ),
) -> None:
    """Fetch and display metadata for a video without downloading.

    Example:
        vdl info "https://youtu.be/x7X9w_GIm1s"
    """
    settings = load_settings()

    try:
        setup_network(settings, network_override=network)
        video_info = fetch_info(url=url, settings=settings)
        render_info(video_info)
    except KeyboardInterrupt:
        print_warning("Operation cancelled by user.")
        sys.exit(130)
    except VdlError as exc:
        if _DEBUG_MODE:
            console.print_exception()
        else:
            print_error(exc.message)
        sys.exit(exc.exit_code)
    except Exception as exc:
        if _DEBUG_MODE:
            console.print_exception()
        else:
            print_error(f"Unexpected error: {exc}")
        sys.exit(1)


@app.command()
def formats(
    url: str = typer.Argument(..., help="Media URL to list formats for."),
    network: str | None = typer.Option(
        None, "--network", help="Network mode: 'direct' or 'tor' (overrides config)."
    ),
    all_formats: bool = typer.Option(
        False, "--all", help="Include storyboard and preview formats in the listing."
    ),
) -> None:
    """List all available formats for a video URL.

    Example:
        vdl formats "https://youtu.be/x7X9w_GIm1s" --all
    """
    settings = load_settings()

    try:
        setup_network(settings, network_override=network)
        fmt_list = list_formats(
            url=url,
            settings=settings,
            include_storyboards=all_formats,
        )
        if not fmt_list:
            print_warning("No formats found for this URL.")
            return
        render_formats_table(fmt_list)
    except KeyboardInterrupt:
        print_warning("Operation cancelled by user.")
        sys.exit(130)
    except VdlError as exc:
        if _DEBUG_MODE:
            console.print_exception()
        else:
            print_error(exc.message)
        sys.exit(exc.exit_code)
    except Exception as exc:
        if _DEBUG_MODE:
            console.print_exception()
        else:
            print_error(f"Unexpected error: {exc}")
        sys.exit(1)


@app.command()
def audio(
    url: str = typer.Argument(..., help="Media URL to extract audio from."),
    format: AudioFormat | None = typer.Option(
        None,
        "-f",
        "--format",
        help="Target audio format (mp3, m4a, opus, best). Defaults to configured default_audio_format.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "-o",
        "--output",
        "--output-dir",
        help="Directory where downloaded audio should be saved.",
    ),
    network: str | None = typer.Option(
        None, "--network", help="Network mode: 'direct' or 'tor' (overrides config)."
    ),
) -> None:
    """Download and extract audio from a media URL.

    Examples:
        vdl audio "https://youtu.be/x7X9w_GIm1s" -f mp3
        vdl audio "https://youtu.be/x7X9w_GIm1s" -f opus -o ./music
    """
    settings = load_settings()
    if output_dir:
        settings.download_dir = output_dir.expanduser()

    target_format = format.value if format is not None else settings.default_audio_format

    try:
        setup_network(settings, network_override=network)
        with RichProgressHandler(console=console) as progress_handler:
            result = download_audio(
                url=url,
                settings=settings,
                audio_format=target_format,
                progress_hooks=[progress_handler],
                postprocessor_hooks=[progress_handler.postprocessor_hook],
            )
        print_success("Successfully extracted audio", title=result.title, path=result.filepath)
    except KeyboardInterrupt:
        print_warning("Operation cancelled by user.")
        sys.exit(130)
    except VdlError as exc:
        if _DEBUG_MODE:
            console.print_exception()
        else:
            print_error(exc.message)
        sys.exit(exc.exit_code)
    except Exception as exc:
        if _DEBUG_MODE:
            console.print_exception()
        else:
            print_error(f"Unexpected error: {exc}")
        sys.exit(1)


@app.command()
def doctor() -> None:
    """Diagnose system environment, external dependencies, and network connectivity.

    Example:
        vdl doctor
    """
    settings = load_settings()
    exit_code = run_doctor(settings=settings, console=console)
    if exit_code != 0:
        sys.exit(exit_code)


@app.command()
def version() -> None:
    """Display installed vdl version.

    Example:
        vdl version
    """
    console.print(f"vdl version {__version__}")


# --- Config Subcommands ---

config_app = typer.Typer(
    name="config",
    help="Manage persistent application configuration.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Show current configuration settings.

    Example:
        vdl config show
    """
    settings = load_settings()
    from rich.table import Table

    table = Table(title="vdl Configuration", border_style="cyan")
    table.add_column("Setting", style="bold cyan")
    table.add_column("Value", style="white")

    for key, val in sorted(asdict(settings).items()):
        table.add_row(key, str(val))

    console.print(table)
    console.print(f"[dim]Config file:[/] {escape(str(get_config_path()))}")


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Configuration key to update."),
    value: str = typer.Argument(..., help="New value for the key."),
) -> None:
    """Update a persistent configuration setting.

    Example:
        vdl config set default_quality 1080p
    """
    settings = load_settings()
    try:
        updated, parsed_val = set_setting(settings, key, value)
        save_settings(updated)
        print_success("Configuration updated", title=f"{key} = {parsed_val}")
    except ConfigError as exc:
        print_error(exc.message)
        sys.exit(exc.exit_code)
    except Exception as exc:
        print_error(f"Failed to set configuration: {exc}")
        sys.exit(1)


@config_app.command("reset")
def config_reset() -> None:
    """Reset configuration to default values.

    Example:
        vdl config reset
    """
    try:
        reset_settings()
        print_success("Configuration reset to default values")
    except Exception as exc:
        print_error(f"Failed to reset configuration: {exc}")
        sys.exit(1)


@config_app.command("path")
def config_path() -> None:
    """Print the path to the configuration file.

    Example:
        vdl config path
    """
    console.print(str(get_config_path()))


if __name__ == "__main__":
    app()
