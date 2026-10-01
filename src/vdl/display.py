"""Terminal display, Rich formatting, and centralized escaping for vdl."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

from vdl.downloader import FormatInfo, VideoInfo, format_size

logger = logging.getLogger(__name__)

console = Console()
err_console = Console(stderr=True)


def print_success(message: str, title: str | None = None, path: Path | str | None = None) -> None:
    """Print success message with safely escaped title and path."""
    text = f"[bold green]{escape(message)}[/bold green]"
    if title:
        text += f": {escape(title)}"
    console.print(text)
    if path:
        console.print(f"[dim]Saved to:[/] {escape(str(path))}")


def print_error(message: str) -> None:
    """Print error message with safely escaped text to stderr."""
    err_console.print(f"[bold red]Error:[/] {escape(message)}")


def print_warning(message: str) -> None:
    """Print warning message with safely escaped text to stderr."""
    err_console.print(f"[yellow]Warning:[/] {escape(message)}")


def render_info(info: VideoInfo) -> None:
    """Render metadata for a video inside a Rich Panel with all values safely escaped."""
    table = Table.grid(padding=(0, 2))
    table.add_column("Field", style="bold cyan", justify="right")
    table.add_column("Value", style="white")

    table.add_row("Title", escape(info.title))
    table.add_row("Uploader", escape(info.uploader))
    table.add_row("Duration", escape(info.duration_string))
    table.add_row("File Size", escape(info.filesize_display))
    table.add_row(
        "Resolutions",
        escape(", ".join(info.resolutions) if info.resolutions else "unknown"),
    )
    if info.thumbnail:
        table.add_row("Thumbnail", escape(info.thumbnail))
    table.add_row("Video ID", escape(info.id))

    panel = Panel(
        table,
        title=f"[bold]Media Information: {escape(info.id)}[/bold]",
        border_style="cyan",
        expand=False,
    )
    console.print(panel)


def render_formats_table(formats: list[FormatInfo], title: str = "Available Formats") -> None:
    """Render a Rich table of available formats with all values escaped."""
    table = Table(title=escape(title), border_style="cyan", show_lines=False)
    table.add_column("ID", style="bold", justify="right")
    table.add_column("Ext", style="green")
    table.add_column("Resolution", justify="right")
    table.add_column("FPS", justify="right")
    table.add_column("Video Codec")
    table.add_column("Audio Codec")
    table.add_column("Size", justify="right")
    table.add_column("Type", style="dim")

    for f in formats:
        fps_str = str(int(f.fps)) if f.fps else ""
        vcodec = escape(f.vcodec or "")
        acodec = escape(f.acodec or "")
        note = escape(f.note or "") if f.note else ""

        # Type with optional note
        type_str = escape(f.type_label)
        if note:
            type_str = f"{type_str} ({note})"

        table.add_row(
            escape(f.format_id),
            escape(f.ext),
            escape(f.resolution),
            fps_str,
            vcodec,
            acodec,
            escape(f.size_display),
            type_str,
        )

    console.print(table)


class DownloadSizeColumn(ProgressColumn):
    """Renders downloaded / total size as 'X.XX MiB / Y.YY MiB' or 'X.XX MiB / unknown'."""

    def render(self, task: Task) -> Text:
        if task.completed is not None and task.completed > 0:
            completed_str = format_size(task.completed)
            if task.total is not None and task.total > 0:
                total_str = format_size(task.total)
            else:
                total_str = "unknown"
            return Text(f"{completed_str} / {total_str}", style="progress.download")
        elif task.total is not None and task.total > 0:
            total_str = format_size(task.total)
            return Text(f"0.00 B / {total_str}", style="progress.download")
        return Text("")


class RichProgressHandler:
    """yt-dlp progress_hooks and postprocessor_hooks compatible handler managing a Rich Progress bar."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self.download_size_col = DownloadSizeColumn()
        self.speed_col = TransferSpeedColumn()
        self.eta_col = TimeRemainingColumn()
        self.progress_col = TaskProgressColumn()

        spinner_name = "line" if sys.platform == "win32" else "dots"
        self.progress = Progress(
            SpinnerColumn(spinner_name),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=30),
            self.progress_col,
            self.download_size_col,
            self.speed_col,
            self.eta_col,
            console=self.console,
            transient=False,
        )
        self.task_id: TaskID | None = None
        self._started: bool = False
        self.last_status: str | None = None
        self.last_phase: str | None = None
        self.last_description: str | None = None
        self.last_downloaded: int | None = None
        self.last_total: int | None = None
        self.last_speed: float | None = None
        self.last_eta: float | None = None

    def __enter__(self) -> RichProgressHandler:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def start(self) -> None:
        """Start the Rich progress display."""
        if not self._started:
            self.progress.start()
            self._started = True

    def stop(self) -> None:
        """Stop the Rich progress display and ensure cursor is visible."""
        if self._started:
            self.progress.stop()
            self._started = False
        try:
            self.console.show_cursor(True)
        except Exception:
            pass

    def __call__(self, d: dict[str, Any]) -> None:
        """Progress hook entry point called by yt-dlp."""
        try:
            self._handle_progress(d)
        except Exception as exc:
            logger.debug("Non-fatal error in progress hook: %s", exc)

    def postprocessor_hook(self, d: dict[str, Any]) -> None:
        """Postprocessor hook entry point called by yt-dlp."""
        try:
            self._handle_postprocessor(d)
        except Exception as exc:
            logger.debug("Non-fatal error in postprocessor hook: %s", exc)

    def _handle_progress(self, d: dict[str, Any]) -> None:
        self.start()
        status = d.get("status")
        self.last_status = status

        raw_filename = d.get("filename") or "media"
        filename = Path(raw_filename).name
        clean_name = escape(filename)

        info = d.get("info_dict") or {}
        vcodec = info.get("vcodec")
        acodec = info.get("acodec")
        if vcodec and vcodec != "none" and (not acodec or acodec == "none"):
            stream_type = "video stream"
        elif acodec and acodec != "none" and (not vcodec or vcodec == "none"):
            stream_type = "audio stream"
        else:
            stream_type = "stream"

        self.last_phase = stream_type

        downloaded = d.get("downloaded_bytes", 0)
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        speed = d.get("speed")
        eta = d.get("eta")

        self.last_downloaded = downloaded
        self.last_total = total
        self.last_speed = speed
        self.last_eta = eta

        if status == "downloading":
            description = f"Downloading {stream_type}: {clean_name}"
            self.last_description = description
            if self.task_id is None:
                self.task_id = self.progress.add_task(
                    description,
                    total=total,
                    completed=downloaded,
                )
            else:
                self.progress.update(
                    self.task_id,
                    description=description,
                    total=total,
                    completed=downloaded,
                )
        elif status == "finished":
            final_total = total or downloaded
            description = f"Finished downloading {stream_type}: {clean_name}"
            self.last_description = description
            if self.task_id is None:
                self.task_id = self.progress.add_task(
                    description,
                    total=final_total,
                    completed=final_total,
                )
            else:
                self.progress.update(
                    self.task_id,
                    description=description,
                    total=final_total,
                    completed=final_total,
                )
        elif status == "error":
            description = f"Download error on {clean_name}"
            self.last_description = description
            if self.task_id is not None:
                self.progress.update(self.task_id, description=description)

    def _handle_postprocessor(self, d: dict[str, Any]) -> None:
        self.start()
        status = d.get("status")
        pp = d.get("postprocessor", "Processing")
        self.last_status = status
        self.last_phase = f"postprocessing:{pp}"

        # MoveFiles is a momentary local rename after download is already finished.
        # Don't overwrite the completed 100% download bar with an indeterminate MoveFiles bar.
        if pp == "MoveFiles":
            return

        if status == "started":
            if pp == "Merger":
                desc = "Merging video and audio streams with FFmpeg..."
            elif pp == "ExtractAudio":
                desc = "Extracting audio with FFmpeg..."
            else:
                desc = f"Processing ({escape(pp)})..."

            self.last_description = desc
            if self.task_id is None:
                self.task_id = self.progress.add_task(desc, total=None)
            else:
                self.progress.update(self.task_id, total=None, completed=0, description=desc)
        elif status == "finished":
            desc = f"Completed {escape(pp)}"
            self.last_description = desc
            if self.task_id is not None:
                self.progress.update(self.task_id, description=desc)

    @property
    def current_task(self) -> Task | None:
        """Get the active Rich Task instance if present."""
        if self.task_id is not None:
            for task in self.progress.tasks:
                if task.id == self.task_id:
                    return task
        return None
