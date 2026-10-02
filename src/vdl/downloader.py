"""Core downloader module wrapping yt-dlp with type-safe interfaces."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp
from rich.console import Console
from yt_dlp.utils import (
    DownloadError as YtDlpDownloadError,
)
from yt_dlp.utils import (
    ExtractorError as YtDlpExtractorError,
)
from yt_dlp.utils import (
    GeoRestrictedError as YtDlpGeoRestrictedError,
)
from yt_dlp.utils import (
    UnsupportedError,
)

from vdl.config import Settings
from vdl.exceptions import (
    DownloadError,
    ExtractorError,
    FormatNotFoundError,
    GeoRestrictedError,
    LoginRequiredError,
    NetworkError,
    UnsupportedUrlError,
)
from vdl.media import get_ffmpeg_path, get_js_runtime_install_hint, require_ffmpeg

logger = logging.getLogger(__name__)
ytdlp_logger = logging.getLogger("vdl.ytdlp")

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI color/style escape sequences from string."""
    return ANSI_ESCAPE_RE.sub("", text)


class YtDlpLoggerBridge:
    """Redirect yt-dlp log calls to standard Python logging with ANSI stripping and prefix cleanup."""

    def __init__(self) -> None:
        self.seen_js_runtime_warning: bool = False
        self.captured_warnings: list[str] = []
        self.captured_errors: list[str] = []

    def debug(self, msg: str) -> None:
        clean = strip_ansi(msg)
        ytdlp_logger.debug(clean)

    def info(self, msg: str) -> None:
        clean = strip_ansi(msg)
        ytdlp_logger.info(clean)

    def warning(self, msg: str) -> None:
        clean = strip_ansi(msg)
        # Strip redundant "WARNING: " prefix
        clean = re.sub(r"^WARNING:\s*", "", clean).strip()
        if "no supported javascript runtime" in clean.lower():
            self.seen_js_runtime_warning = True
        self.captured_warnings.append(clean)
        ytdlp_logger.warning(clean)

    def error(self, msg: str) -> None:
        clean = strip_ansi(msg)
        # Strip redundant "ERROR: " prefix to avoid "ERROR: ERROR:"
        clean = re.sub(r"^ERROR:\s*", "", clean).strip()
        self.captured_errors.append(clean)
        ytdlp_logger.error(clean)


@dataclass(frozen=True)
class FormatInfo:
    """Representation of an available stream format."""

    format_id: str
    ext: str
    resolution: str
    height: int | None = None
    fps: int | float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    filesize: int | None = None
    filesize_approx: int | None = None
    tbr: float | None = None
    note: str | None = None
    is_video: bool = False
    is_audio: bool = False

    @property
    def is_combined(self) -> bool:
        """True if the format contains both video and audio streams."""
        return self.is_video and self.is_audio

    @property
    def is_video_only(self) -> bool:
        """True if format has video but no audio stream."""
        return self.is_video and not self.is_audio

    @property
    def is_audio_only(self) -> bool:
        """True if format has audio but no video stream."""
        return self.is_audio and not self.is_video

    @property
    def best_filesize(self) -> int | None:
        """Return exact filesize if available, else approx filesize."""
        return self.filesize if self.filesize is not None else self.filesize_approx

    @property
    def size_display(self) -> str:
        """Human-readable size with ~ prefix for approximate values."""
        if self.filesize is not None:
            return format_size(self.filesize)
        if self.filesize_approx is not None:
            formatted = format_size(self.filesize_approx)
            if formatted != "unknown":
                return f"~{formatted}"
        return "unknown"

    @property
    def is_storyboard(self) -> bool:
        """True if format is a storyboard (mhtml with no audio and no video codecs)."""
        return self.ext == "mhtml" or (not self.is_video and not self.is_audio)

    @property
    def type_label(self) -> str:
        """Human-readable type label for the format."""
        if self.is_storyboard:
            return "storyboard"
        if self.is_combined:
            return "combined"
        if self.is_video_only:
            return "video only"
        if self.is_audio_only:
            return "audio only"
        return "unknown"


@dataclass(frozen=True)
class VideoInfo:
    """Metadata extracted for a video without downloading."""

    id: str
    title: str
    uploader: str
    duration: int | float | None
    duration_string: str
    thumbnail: str | None
    resolutions: list[str]
    formats: list[FormatInfo] = field(default_factory=list)
    filesize: int | None = None
    filesize_approx: int | None = None

    @property
    def filesize_display(self) -> str:
        """Human-readable size: exact, ~approximate, or 'unknown'."""
        if self.filesize is not None:
            return format_size(self.filesize)
        if self.filesize_approx is not None:
            formatted = format_size(self.filesize_approx)
            if formatted != "unknown":
                return f"~{formatted}"
        return "unknown"


@dataclass(frozen=True)
class DownloadResult:
    """Carries success data for a completed download."""

    filepath: Path
    title: str


def format_duration(seconds: float | None) -> str:
    """Format duration in seconds into HH:MM:SS or MM:SS."""
    if seconds is None or seconds < 0:
        return "unknown"
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_size(bytes_num: float | None) -> str:
    """Format byte count into human-readable MiB/GiB string or 'unknown'."""
    if bytes_num is None or bytes_num <= 0:
        return "unknown"

    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    val = float(bytes_num)
    unit_idx = 0
    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1
    return f"{val:.2f} {units[unit_idx]}"


QUALITY_RE = re.compile(r"^(\d{3,4})p$|^best$")
CODEC_MAP = {
    "h264": "vcodec~='^(avc|h264)'",
    "av1": "vcodec~='^av0?1'",
}


def build_format_selector(
    quality: str | None = None,
    format_id: str | None = None,
    is_video_only: bool = False,
    allow_merge: bool = True,
    prefer_codec: str | None = None,
) -> str:
    r"""Build a yt-dlp format selector string.

    Validates quality against ^\d{3,4}p$|^best$, adds final /b fallback,
    and appends +ba when format_id is a video-only stream.

    Args:
        quality: Resolution like '720p' or 'best'.
        format_id: Exact yt-dlp format ID.
        is_video_only: Whether format_id is video-only (appends +ba).
        allow_merge: Whether FFmpeg merge is allowed.
        prefer_codec: Codec preference: 'h264', 'av1', or 'any' (default).
    """
    if format_id:
        if is_video_only:
            return f"{format_id}+ba/b"
        return format_id

    qual = quality or "best"
    match = QUALITY_RE.match(qual)
    if not match:
        raise FormatNotFoundError(
            f"Invalid quality specification '{qual}'. Must match 1080p, 720p, 480p, or 'best'."
        )

    codec_filter = ""
    codec = (prefer_codec or "any").lower()
    if codec in CODEC_MAP:
        codec_filter = f"[{CODEC_MAP[codec]}]"

    if qual == "best":
        if allow_merge:
            if codec_filter:
                return f"bv*{codec_filter}+ba/bv*+ba/b"
            return "bv*+ba/b"
        return "b"

    # Specific height
    height = match.group(1)
    if allow_merge:
        if codec_filter:
            return (
                f"bv*[height<={height}]{codec_filter}+ba"
                f"/bv*[height<={height}]+ba"
                f"/b[height<={height}]/b"
            )
        return f"bv*[height<={height}]+ba/b[height<={height}]/b"
    return f"b[height<={height}]/b"


SUPPORTED_AUDIO_FORMATS = {"mp3", "m4a", "opus", "best"}


def should_stream_copy(target_format: str, source_codec: str | None) -> bool:
    """Determine if audio extraction can stream-copy (remux) without re-encoding.

    Args:
        target_format: Target format ('mp3', 'm4a', 'opus', 'best').
        source_codec: Source audio codec (e.g., 'aac', 'mp4a.40.2', 'opus', 'mp3').

    Returns:
        True if the stream can be copied losslessly, False if re-encoding is technically required.
    """
    target = target_format.lower()
    if target == "best":
        return True
    if not source_codec or source_codec == "none":
        return False

    src = source_codec.lower()
    if target == "m4a":
        return src.startswith("mp4a") or src.startswith("aac")
    if target == "opus":
        return "opus" in src
    if target == "mp3":
        return src == "mp3" or "mp3" in src

    return False


def build_audio_format_selector(audio_format: str = "best") -> str:
    """Build yt-dlp format selector prioritizing streams that avoid re-encoding."""
    fmt = audio_format.lower()
    if fmt == "m4a":
        return "ba[ext=m4a]/ba[acodec^=mp4a]/ba[acodec^=aac]/ba/b"
    if fmt == "opus":
        return "ba[acodec^=opus]/ba[ext=webm]/ba/b"
    return "ba/b"


def find_nearest_quality(
    requested: str, available_formats: list[FormatInfo]
) -> tuple[str, str | None]:
    """Find the nearest available quality at or below the requested height.

    Args:
        requested: Quality string like '1080p'.
        available_formats: List of FormatInfo from list_formats().

    Returns:
        (effective_quality, warning_message): The quality to use and an optional
        message explaining the fallback. Returns ('best', None) if no heights found.
    """
    match = QUALITY_RE.match(requested)
    if not match or requested == "best":
        return (requested, None)

    target_height = int(match.group(1))
    # Collect unique heights from non-storyboard video formats
    heights = sorted(
        {f.height for f in available_formats if f.height and not f.is_storyboard},
        reverse=True,
    )
    if not heights:
        return (requested, None)

    # Exact match
    if target_height in heights:
        return (requested, None)

    # Find nearest lower height
    lower = [h for h in heights if h <= target_height]
    if lower:
        best = lower[0]
        return (
            f"{best}p",
            f"Requested {requested} not available. Using nearest lower quality: {best}p.",
        )

    # Nothing at or below the target; use best available
    return ("best", f"Requested {requested} not available. Using best available quality.")


def list_formats(
    url: str, settings: Settings, include_storyboards: bool = False
) -> list[FormatInfo]:
    """Fetch and return parsed format list for a URL.

    Args:
        url: Media URL.
        settings: Application settings.
        include_storyboards: If True, include storyboard formats (ext=mhtml).

    Returns:
        List of FormatInfo dataclasses, no raw yt-dlp dicts leak.
    """
    bridge = YtDlpLoggerBridge()
    ydl_opts = build_ydl_opts(
        settings,
        extra_opts={"noplaylist": True},
        logger_bridge=bridge,
    )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            if not info_dict:
                raise DownloadError(f"No video information returned for '{url}'")

            # If playlist is returned, pick first entry
            if info_dict.get("_type") == "playlist":
                entries = info_dict.get("entries")
                if entries:
                    info_dict = next((e for e in entries if e), info_dict)

            raw_formats = info_dict.get("formats") or []
            parsed = [parse_format(f) for f in raw_formats]

            if not include_storyboards:
                parsed = [f for f in parsed if not f.is_storyboard]

            return parsed
    except Exception as exc:
        raise _translate_ytdlp_error(exc, url, logger_bridge=bridge) from exc


def parse_format(fmt: dict[str, Any]) -> FormatInfo:
    """Parse raw yt-dlp format dictionary into FormatInfo dataclass."""
    fid = str(fmt.get("format_id", ""))
    ext = str(fmt.get("ext", "unknown"))
    vcodec = fmt.get("vcodec")
    acodec = fmt.get("acodec")
    raw_height = fmt.get("height")

    has_video = vcodec not in (None, "none")
    has_audio = acodec not in (None, "none")

    # Resolution
    res = fmt.get("resolution")
    if not res:
        width = fmt.get("width")
        height = fmt.get("height")
        if width and height:
            res = f"{width}x{height}"
        elif height:
            res = f"{height}p"
        elif has_audio and not has_video:
            res = "audio only"
        else:
            res = "unknown"

    return FormatInfo(
        format_id=fid,
        ext=ext,
        resolution=str(res),
        height=int(raw_height) if isinstance(raw_height, (int, float)) and raw_height > 0 else None,
        fps=fmt.get("fps"),
        vcodec=str(vcodec) if vcodec else None,
        acodec=str(acodec) if acodec else None,
        filesize=fmt.get("filesize"),
        filesize_approx=fmt.get("filesize_approx"),
        tbr=fmt.get("tbr"),
        note=fmt.get("format_note"),
        is_video=has_video,
        is_audio=has_audio,
    )


def build_ydl_opts(
    settings: Settings,
    progress_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
    postprocessor_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
    extra_opts: dict[str, Any] | None = None,
    logger_bridge: YtDlpLoggerBridge | None = None,
) -> dict[str, Any]:
    """Construct yt-dlp options dictionary with production defaults.

    Note:
        - rate_limit from Settings is passed directly as 'ratelimit' (bytes/second).
        - Proxy options are read directly from settings.proxy.
    """
    download_dir = str(settings.download_dir.expanduser().resolve())
    outtmpl = f"{download_dir}/%(title)s [%(id)s].%(ext)s"

    opts: dict[str, Any] = {
        "outtmpl": outtmpl,
        "quiet": True,
        "noprogress": True,
        "no_color": True,
        "logger": logger_bridge or YtDlpLoggerBridge(),
        "retries": settings.retries,
        # Applied directly to yt-dlp options in bytes per second
        "ratelimit": settings.rate_limit,
        "restrictfilenames": settings.restrict_filenames,
        "writethumbnail": settings.write_thumbnail,
        "windowsfilenames": settings.windowsfilenames,
        "noplaylist": settings.noplaylist,
        # Never bypass security or harvest browser cookies
        "cookiesfrombrowser": None,
    }

    ffmpeg_bin = get_ffmpeg_path()
    if ffmpeg_bin:
        opts["ffmpeg_location"] = ffmpeg_bin

    if settings.proxy:
        opts["proxy"] = settings.proxy

    if progress_hooks:
        opts["progress_hooks"] = progress_hooks

    if postprocessor_hooks:
        opts["postprocessor_hooks"] = postprocessor_hooks

    if extra_opts:
        opts.update(extra_opts)

    return opts


def _translate_ytdlp_error(
    exc: Exception, url: str, logger_bridge: YtDlpLoggerBridge | None = None
) -> Exception:
    """Translate yt-dlp exceptions into domain-specific VdlErrors with clean messages and hints."""
    raw_msg = str(exc)
    clean_msg = strip_ansi(raw_msg)
    # Strip any leading 'ERROR:' or 'ERROR: ERROR:' prefixes from yt-dlp exception string
    clean_msg = re.sub(r"^(?:ERROR:\s*)+", "", clean_msg).strip()
    msg_lower = clean_msg.lower()

    if isinstance(exc, UnsupportedError) or "unsupported url" in msg_lower:
        return UnsupportedUrlError(f"Unsupported URL: '{url}'. No extractor found.")
    if "proxy" in msg_lower or "socks" in msg_lower or "connection refused" in msg_lower:
        return NetworkError(f"Network error accessing '{url}': {clean_msg}")

    # Check for geographic restrictions
    if (
        isinstance(exc, YtDlpGeoRestrictedError)
        or "geo-restricted" in msg_lower
        or "not available in your country" in msg_lower
        or "blocked in your country" in msg_lower
        or "uploader has not made this video available in your country" in msg_lower
    ):
        return GeoRestrictedError(
            f"Content is geographically restricted for '{url}': {clean_msg}\n\n"
            "Hint: The content is blocked in your current region. Try using Tor mode (--network tor) or a VPN."
        )

    # Check for login, authentication, or age restrictions
    if (
        "sign in to confirm your age" in msg_lower
        or "age-restricted" in msg_lower
        or "confirm your age" in msg_lower
        or "private video" in msg_lower
        or "this video requires payment" in msg_lower
        or "login required" in msg_lower
        or "requires authentication" in msg_lower
    ):
        return LoginRequiredError(
            f"Authentication required for '{url}': {clean_msg}\n\n"
            "Hint: This video is private, paid, or age-restricted. vdl intentionally does not harvest "
            "browser cookies or user credentials to protect your account security."
        )

    # Detect JS runtime warning + format unavailable condition
    seen_js_warning = (logger_bridge is not None and logger_bridge.seen_js_runtime_warning) or (
        "no supported javascript runtime" in msg_lower
    )
    if seen_js_warning and "requested format is not available" in msg_lower:
        hint = get_js_runtime_install_hint()
        return DownloadError(
            f"Download failed for '{url}': {clean_msg}\n\n"
            f"Hint: YouTube requires a JavaScript runtime (such as Deno) to extract video formats.\n"
            f"{hint}"
        )

    if isinstance(exc, YtDlpExtractorError):
        return ExtractorError(f"Metadata extraction failed for '{url}': {clean_msg}")

    if isinstance(exc, YtDlpDownloadError):
        return DownloadError(f"Download failed for '{url}': {clean_msg}")
    return DownloadError(f"Unexpected error processing '{url}': {clean_msg}")


def fetch_info(url: str, settings: Settings) -> VideoInfo:
    """Fetch video metadata without downloading using download=False and noplaylist=True.

    Returns:
        VideoInfo plain dataclass. No raw yt-dlp dicts leak outside this function.
    """
    logger.info("Fetching video info for: %s", url)
    bridge = YtDlpLoggerBridge()
    ydl_opts = build_ydl_opts(
        settings,
        extra_opts={"noplaylist": True},
        logger_bridge=bridge,
    )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            if not info_dict:
                raise DownloadError(f"No video information returned for '{url}'")

            # If playlist is returned, pick first entry
            if info_dict.get("_type") == "playlist":
                entries = info_dict.get("entries")
                if entries:
                    info_dict = next((e for e in entries if e), info_dict)

            vid_id = str(info_dict.get("id") or "unknown")
            title = str(info_dict.get("title") or "unknown")
            uploader = str(info_dict.get("uploader") or info_dict.get("channel") or "unknown")
            duration = info_dict.get("duration")
            duration_str = format_duration(duration)
            thumbnail = info_dict.get("thumbnail")
            filesize = info_dict.get("filesize")
            filesize_approx = info_dict.get("filesize_approx")

            # Parse formats
            raw_formats = info_dict.get("formats") or []
            parsed_formats = [parse_format(f) for f in raw_formats]

            # Collect unique video resolutions sorted descending, excluding storyboards
            res_heights: set[int] = set()
            for pf in parsed_formats:
                if pf.is_storyboard:
                    continue
                height = pf.resolution.rstrip("p")
                # Try extracting height from resolution like "1920x1080" or "1080p"
                if "x" in pf.resolution:
                    try:
                        height = pf.resolution.split("x")[1]
                    except (IndexError, ValueError):
                        continue
                try:
                    h = int(height)
                    if h > 0:
                        res_heights.add(h)
                except ValueError:
                    continue

            resolutions = [f"{h}p" for h in sorted(res_heights, reverse=True)]
            if not resolutions:
                res_val = info_dict.get("resolution")
                if res_val:
                    resolutions = [str(res_val)]

            # If top-level filesize is missing, estimate from the best combined format
            if filesize is None and filesize_approx is None:
                for pf in reversed(parsed_formats):
                    if pf.is_combined and pf.best_filesize:
                        if pf.filesize is not None:
                            filesize = pf.filesize
                        else:
                            filesize_approx = pf.filesize_approx
                        break

            return VideoInfo(
                id=vid_id,
                title=title,
                uploader=uploader,
                duration=float(duration) if duration is not None else None,
                duration_string=duration_str,
                thumbnail=str(thumbnail) if thumbnail else None,
                resolutions=resolutions,
                formats=parsed_formats,
                filesize=filesize,
                filesize_approx=filesize_approx,
            )
    except Exception as exc:
        raise _translate_ytdlp_error(exc, url, logger_bridge=bridge) from exc


def download_video(
    url: str,
    settings: Settings,
    quality: str | None = None,
    format_id: str | None = None,
    is_playlist: bool = False,
    progress_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    postprocessor_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
    prefer_codec: str | None = None,
) -> DownloadResult:
    """Download a video using yt-dlp, handling format selection and FFmpeg requirements.

    Raises:
        FFmpegNotFoundError: If FFmpeg is required for merging separate streams but missing.
        DownloadError / UnsupportedUrlError / NetworkError on download issues.
    """
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Starting video download for URL: %s", url)

    has_ffmpeg = bool(get_ffmpeg_path())
    allow_merge = has_ffmpeg

    # If format_id is specified, check if it's video-only so we append +ba
    is_video_only = False
    if format_id and "+" not in format_id:
        try:
            formats = list_formats(url, settings)
            match_fmt = next((f for f in formats if f.format_id == format_id), None)
            if match_fmt and match_fmt.is_video_only:
                is_video_only = True
        except Exception:
            pass  # If metadata fetch fails, proceed without video-only detection

    if not has_ffmpeg and not format_id:
        msg = "FFmpeg not detected; falling back to pre-merged single file format."
        logger.warning(msg)
        console = Console(stderr=True)
        console.print(f"[yellow]Warning:[/] {msg}")

    format_selector = build_format_selector(
        quality=quality or settings.default_quality,
        format_id=format_id,
        is_video_only=is_video_only,
        allow_merge=allow_merge,
        prefer_codec=prefer_codec,
    )

    if "+" in format_selector and not has_ffmpeg:
        require_ffmpeg("merging video and audio streams")

    extra_opts: dict[str, Any] = {
        "format": format_selector,
        "noplaylist": not is_playlist,
    }

    hooks: list[Callable[[dict[str, Any]], None]] = []
    if progress_hooks:
        hooks.extend(progress_hooks)
    if progress_hook:
        hooks.append(progress_hook)

    bridge = YtDlpLoggerBridge()
    ydl_opts = build_ydl_opts(
        settings,
        progress_hooks=hooks if hooks else None,
        postprocessor_hooks=postprocessor_hooks,
        extra_opts=extra_opts,
        logger_bridge=bridge,
    )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            if not info_dict:
                raise DownloadError(f"No video information returned for '{url}'")

            title = info_dict.get("title", "video")

            # 1. Resolve final merged filepath via requested_downloads[0]["filepath"]
            filepath: Path | None = None
            requested_downloads = info_dict.get("requested_downloads")
            if (
                requested_downloads
                and isinstance(requested_downloads, list)
                and len(requested_downloads) > 0
            ):
                req_path = requested_downloads[0].get("filepath")
                if req_path:
                    filepath = Path(req_path)

            # Fall back to prepare_filename() only if requested_downloads is missing
            if not filepath:
                fn = ydl.prepare_filename(info_dict)
                filepath = Path(fn)

            # 2. Verify returned path exists on disk before reporting success
            if not filepath.exists():
                logger.warning(
                    "Expected downloaded file '%s' not found on disk. Searching output dir for video ID.",
                    filepath,
                )
                video_id = info_dict.get("id")
                if video_id:
                    matches = [
                        p
                        for p in settings.download_dir.glob(f"*{video_id}*")
                        if p.is_file() and not p.name.endswith((".part", ".ytdl", ".temp"))
                    ]
                    if matches:
                        # Pick the most recently modified file
                        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                        filepath = matches[0]

            if not filepath.exists():
                raise DownloadError(
                    f"Download completed for '{title}', but target file could not be found at {filepath}"
                )

            return DownloadResult(filepath=filepath, title=title)
    except Exception as exc:
        raise _translate_ytdlp_error(exc, url, logger_bridge=bridge) from exc


def download_audio(
    url: str,
    settings: Settings,
    audio_format: str = "best",
    progress_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    postprocessor_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
) -> DownloadResult:
    """Download and extract audio from a URL, preferring lossless stream copy when possible.

    Args:
        url: Media URL.
        settings: Application settings.
        audio_format: Desired audio format ('mp3', 'm4a', 'opus', 'best').
        progress_hooks: Optional list of progress callbacks.
        progress_hook: Optional single progress callback.
        postprocessor_hooks: Optional list of postprocessor callbacks.

    Returns:
        DownloadResult containing verified filepath and video title.

    Raises:
        FFmpegNotFoundError: If FFmpeg is missing (checked before any network/yt-dlp call).
        FormatNotFoundError: If the requested audio format is invalid.
        DownloadError / UnsupportedUrlError / NetworkError on download issues.
    """
    # 1. Require FFmpeg before anything else
    require_ffmpeg("audio extraction")

    fmt_lower = audio_format.lower()
    if fmt_lower not in SUPPORTED_AUDIO_FORMATS:
        raise FormatNotFoundError(
            f"Unsupported audio format '{audio_format}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}."
        )

    settings.download_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Starting audio download for URL: %s (format: %s)", url, fmt_lower)

    format_selector = build_audio_format_selector(fmt_lower)

    pp_config: dict[str, Any] = {
        "key": "FFmpegExtractAudio",
        "preferredcodec": fmt_lower,
    }
    # preferredquality is only meaningful and applied for lossy re-encoding (e.g. mp3)
    if fmt_lower == "mp3":
        pp_config["preferredquality"] = "0"

    postprocessors: list[dict[str, Any]] = [pp_config]

    extra_opts: dict[str, Any] = {
        "format": format_selector,
        "noplaylist": True,
        "postprocessors": postprocessors,
    }

    hooks: list[Callable[[dict[str, Any]], None]] = []
    if progress_hooks:
        hooks.extend(progress_hooks)
    if progress_hook:
        hooks.append(progress_hook)

    bridge = YtDlpLoggerBridge()
    ydl_opts = build_ydl_opts(
        settings,
        progress_hooks=hooks if hooks else None,
        postprocessor_hooks=postprocessor_hooks,
        extra_opts=extra_opts,
        logger_bridge=bridge,
    )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            if not info_dict:
                raise DownloadError(f"No video information returned for '{url}'")

            title = info_dict.get("title", "audio")

            # 1. Resolve final extracted filepath via requested_downloads[0]["filepath"]
            filepath: Path | None = None
            requested_downloads = info_dict.get("requested_downloads")
            if (
                requested_downloads
                and isinstance(requested_downloads, list)
                and len(requested_downloads) > 0
            ):
                req_path = requested_downloads[0].get("filepath")
                if req_path:
                    filepath = Path(req_path)

            # Fall back to prepare_filename() only if requested_downloads is missing
            if not filepath:
                fn = ydl.prepare_filename(info_dict)
                ext = fmt_lower if fmt_lower != "best" else "m4a"
                filepath = Path(fn).with_suffix(f".{ext}")

            # 2. Verify returned path exists on disk before reporting success
            if not filepath.exists():
                logger.warning(
                    "Expected extracted audio file '%s' not found on disk. Searching output dir for video ID.",
                    filepath,
                )
                video_id = info_dict.get("id")
                if video_id:
                    matches = [
                        p
                        for p in settings.download_dir.glob(f"*{video_id}*")
                        if p.is_file() and not p.name.endswith((".part", ".ytdl", ".temp"))
                    ]
                    if matches:
                        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                        filepath = matches[0]

            if not filepath.exists():
                raise DownloadError(
                    f"Audio extraction completed for '{title}', but target file could not be found at {filepath}"
                )

            return DownloadResult(filepath=filepath, title=title)
    except Exception as exc:
        raise _translate_ytdlp_error(exc, url, logger_bridge=bridge) from exc
