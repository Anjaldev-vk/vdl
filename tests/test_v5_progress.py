"""Unit tests for V5 RichProgressHandler, progress columns, stream phases, and log coexistence."""

import logging
from unittest.mock import MagicMock

from rich.console import Console

from vdl.display import DownloadSizeColumn, RichProgressHandler


def test_download_size_column_known_total():
    """DownloadSizeColumn renders 'downloaded / total' when total is known."""
    col = DownloadSizeColumn()
    task = MagicMock()
    task.completed = 1024 * 1024 * 5  # 5 MiB
    task.total = 1024 * 1024 * 10  # 10 MiB

    rendered = col.render(task)
    assert "5.00 MiB / 10.00 MiB" in rendered.plain


def test_download_size_column_unknown_total():
    """DownloadSizeColumn renders 'downloaded / unknown' when total is None or 0."""
    col = DownloadSizeColumn()
    task = MagicMock()
    task.completed = 1024 * 1024 * 2  # 2 MiB
    task.total = None

    rendered = col.render(task)
    assert "2.00 MiB / unknown" in rendered.plain

    task.total = 0
    rendered2 = col.render(task)
    assert "2.00 MiB / unknown" in rendered2.plain


def test_progress_handler_downloading_with_known_total():
    """Test progress hook with downloading status and known total bytes."""
    console = Console(record=True, width=120)
    handler = RichProgressHandler(console=console)

    payload = {
        "status": "downloading",
        "filename": "video.f137.mp4",
        "downloaded_bytes": 5242880,
        "total_bytes": 10485760,
        "speed": 1048576.0,
        "eta": 5,
        "info_dict": {"vcodec": "avc1.640028", "acodec": "none"},
    }

    handler(payload)
    assert handler.last_status == "downloading"
    assert handler.last_phase == "video stream"
    assert "Downloading video stream: video.f137.mp4" in handler.last_description
    assert handler.last_downloaded == 5242880
    assert handler.last_total == 10485760
    assert handler.last_speed == 1048576.0
    assert handler.last_eta == 5

    task = handler.current_task
    assert task is not None
    assert task.total == 10485760
    assert task.completed == 5242880
    assert task.percentage == 50.0

    handler.stop()


def test_progress_handler_downloading_with_unknown_total():
    """Test progress hook with downloading status when total bytes is unavailable."""
    console = Console(record=True, width=120)
    handler = RichProgressHandler(console=console)

    payload = {
        "status": "downloading",
        "filename": "stream.mp4",
        "downloaded_bytes": 2097152,
        "total_bytes": None,
        "total_bytes_estimate": None,
        "speed": 524288.0,
        "eta": None,
        "info_dict": {"vcodec": "none", "acodec": "opus"},
    }

    handler(payload)
    assert handler.last_status == "downloading"
    assert handler.last_phase == "audio stream"
    assert "Downloading audio stream: stream.mp4" in handler.last_description
    assert handler.last_downloaded == 2097152
    assert handler.last_total is None

    task = handler.current_task
    assert task is not None
    assert task.total is None
    assert task.completed == 2097152

    # DownloadSizeColumn renders completed / unknown without crashing
    rendered_size = handler.download_size_col.render(task)
    assert "2.00 MiB / unknown" in rendered_size.plain

    handler.stop()


def test_progress_handler_stream_phase_switching():
    """Verify handler cleanly updates between separate video and audio streams."""
    console = Console(record=True, width=120)
    handler = RichProgressHandler(console=console)

    # 1. Video stream starts
    handler(
        {
            "status": "downloading",
            "filename": "video.f137.mp4",
            "downloaded_bytes": 1000,
            "total_bytes": 2000,
            "info_dict": {"vcodec": "avc1", "acodec": "none"},
        }
    )
    assert handler.last_phase == "video stream"
    assert "Downloading video stream: video.f137.mp4" in handler.last_description

    # 2. Video stream finishes
    handler(
        {
            "status": "finished",
            "filename": "video.f137.mp4",
            "downloaded_bytes": 2000,
            "total_bytes": 2000,
            "info_dict": {"vcodec": "avc1", "acodec": "none"},
        }
    )
    assert handler.last_status == "finished"
    assert "Finished downloading video stream: video.f137.mp4" in handler.last_description

    # 3. Audio stream starts
    handler(
        {
            "status": "downloading",
            "filename": "audio.f140.m4a",
            "downloaded_bytes": 500,
            "total_bytes": 1000,
            "info_dict": {"vcodec": "none", "acodec": "mp4a"},
        }
    )
    assert handler.last_phase == "audio stream"
    assert "Downloading audio stream: audio.f140.m4a" in handler.last_description

    # 4. Audio stream finishes
    handler(
        {
            "status": "finished",
            "filename": "audio.f140.m4a",
            "downloaded_bytes": 1000,
            "total_bytes": 1000,
            "info_dict": {"vcodec": "none", "acodec": "mp4a"},
        }
    )
    assert "Finished downloading audio stream: audio.f140.m4a" in handler.last_description

    handler.stop()


def test_progress_handler_merge_and_postprocessor_phase():
    """Verify merge phase renders without crashing when no byte progress is available."""
    console = Console(record=True, width=120)
    handler = RichProgressHandler(console=console)

    # Postprocessor Merger started
    handler.postprocessor_hook(
        {
            "status": "started",
            "postprocessor": "Merger",
        }
    )
    assert handler.last_status == "started"
    assert handler.last_phase == "postprocessing:Merger"
    assert "Merging video and audio streams with FFmpeg..." in handler.last_description

    task = handler.current_task
    assert task is not None
    # Indeterminate state
    assert task.total is None
    assert task.completed == 0

    # Postprocessor finished
    handler.postprocessor_hook(
        {
            "status": "finished",
            "postprocessor": "Merger",
        }
    )
    assert handler.last_status == "finished"
    assert "Completed Merger" in handler.last_description

    handler.stop()


def test_progress_handler_extract_audio_phase():
    """Verify ExtractAudio postprocessor phase updates description appropriately."""
    console = Console(record=True, width=120)
    handler = RichProgressHandler(console=console)

    handler.postprocessor_hook(
        {
            "status": "started",
            "postprocessor": "ExtractAudio",
        }
    )
    assert "Extracting audio with FFmpeg..." in handler.last_description
    assert handler.current_task.total is None

    handler.postprocessor_hook(
        {
            "status": "finished",
            "postprocessor": "ExtractAudio",
        }
    )
    assert "Completed ExtractAudio" in handler.last_description
    handler.stop()


def test_progress_handler_brackets_escaped():
    """Verify filenames with brackets are escaped and do not crash or corrupt markup."""
    console = Console(record=True, width=120)
    handler = RichProgressHandler(console=console)

    payload = {
        "status": "downloading",
        "filename": "C:/path/Video [Official] [x7X9w_GIm1s].f137.mp4",
        "downloaded_bytes": 100,
        "total_bytes": 200,
    }

    handler(payload)
    assert "[Official]" in handler.last_description
    assert "[x7X9w_GIm1s]" in handler.last_description
    handler.stop()


def test_progress_handler_error_status():
    """Verify error status in progress hook updates task description without raising."""
    console = Console(record=True, width=120)
    handler = RichProgressHandler(console=console)

    payload = {
        "status": "error",
        "filename": "failing_video.mp4",
    }
    handler(payload)
    assert "Download error on failing_video.mp4" in handler.last_description
    handler.stop()


def test_progress_handler_exception_safety():
    """Assert hook errors (e.g. malformed payloads) don't crash the download (log and continue)."""
    console = Console(record=True, width=120)
    handler = RichProgressHandler(console=console)

    # Malformed payload (e.g. None or object without expected attributes)
    handler(None)  # Should catch exception, log, and return cleanly without raising
    handler.postprocessor_hook(None)  # Should catch and return cleanly

    handler.stop()


def test_progress_handler_context_manager():
    """Verify RichProgressHandler functions properly as a context manager."""
    console = Console(record=True, width=120)
    with RichProgressHandler(console=console) as handler:
        assert handler._started is True
        handler(
            {
                "status": "downloading",
                "filename": "test.mp4",
                "downloaded_bytes": 50,
                "total_bytes": 100,
            }
        )
        assert handler.last_downloaded == 50
    # After exiting context manager, handler is stopped
    assert handler._started is False


def test_progress_handler_and_logging_coexistence(caplog):
    """Assert logging lines and progress bar coexist cleanly without error."""
    console = Console(record=True, width=120)
    logger = logging.getLogger("vdl.test_coexistence")

    with RichProgressHandler(console=console) as handler:
        with caplog.at_level(logging.DEBUG):
            logger.info("Informational message during download")
            logger.debug("Debug message during download")

            handler(
                {
                    "status": "downloading",
                    "filename": "sample.mp4",
                    "downloaded_bytes": 500,
                    "total_bytes": 1000,
                }
            )

            logger.warning("Warning message during download")

    assert "Informational message during download" in caplog.text
    assert "Debug message during download" in caplog.text
    assert "Warning message during download" in caplog.text


def test_progress_handler_speed_and_eta_none_renders_placeholders():
    """Verify first hook call with downloaded_bytes set but speed=None and eta=None renders placeholders without 'None'."""
    console = Console(record=True, width=120)
    handler = RichProgressHandler(console=console)

    payload = {
        "status": "downloading",
        "filename": "init_stream.mp4",
        "downloaded_bytes": 1024,
        "total_bytes": 2048,
        "speed": None,
        "eta": None,
    }

    handler(payload)
    task = handler.current_task
    assert task is not None

    # Render each column and verify no "None" string is produced
    for col in handler.progress.columns:
        rendered = col.render(task)
        text_str = str(rendered)
        assert "None" not in text_str

    # Speed column renders empty or placeholder, TimeRemainingColumn renders placeholder (--:--)
    speed_rendered = handler.speed_col.render(task)
    eta_rendered = handler.eta_col.render(task)
    assert "None" not in str(speed_rendered)
    assert "None" not in str(eta_rendered)

    handler.stop()


def test_progress_handler_total_bytes_none_then_known():
    """Verify handler switches from 'unknown' to real percentage/total without duplicating tasks or crashing."""
    console = Console(record=True, width=120)
    handler = RichProgressHandler(console=console)

    # 1. First hook: total_bytes is None
    payload_1 = {
        "status": "downloading",
        "filename": "video.mp4",
        "downloaded_bytes": 1048576,
        "total_bytes": None,
    }
    handler(payload_1)
    assert len(handler.progress.tasks) == 1
    task = handler.current_task
    assert task is not None
    assert task.total is None
    assert "1.00 MiB / unknown" in handler.download_size_col.render(task).plain

    # 2. Later hook: total_bytes becomes known (4 MiB)
    payload_2 = {
        "status": "downloading",
        "filename": "video.mp4",
        "downloaded_bytes": 2097152,
        "total_bytes": 4194304,
    }
    handler(payload_2)
    # Still only 1 task, updated in-place without duplicates
    assert len(handler.progress.tasks) == 1
    task_after = handler.current_task
    assert task_after is not None
    assert task_after.total == 4194304
    assert task_after.completed == 2097152
    assert task_after.percentage == 50.0
    assert "2.00 MiB / 4.00 MiB" in handler.download_size_col.render(task_after).plain

    handler.stop()


def test_keyboard_interrupt_clean_exit(tmp_path):
    """Verify KeyboardInterrupt during download exits cleanly with code 130 and warning message."""
    from unittest.mock import patch

    from typer.testing import CliRunner

    from vdl.cli import app

    runner = CliRunner()
    with patch("vdl.cli.download_video", side_effect=KeyboardInterrupt):
        result = runner.invoke(app, ["download", "https://example.com/video", "-o", str(tmp_path)])
        assert result.exit_code == 130
        assert "cancelled by user" in result.output.lower()
        assert "traceback" not in result.output.lower()
