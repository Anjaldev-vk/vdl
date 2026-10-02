"""Media processing and external binary helpers (FFmpeg detection)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from vdl.exceptions import FFmpegNotFoundError


def _find_binary(name: str) -> str | None:
    """Find a binary by checking next to the executable / frozen root, then PATH.

    Checks:
    1. Directory of sys.executable (for frozen PyInstaller onedir distribution)
    2. 'bundled-tools' subdirectory next to sys.executable
    3. sys._MEIPASS (for PyInstaller onefile or temporary unpacking)
    4. 'bundled-tools' subdirectory inside sys._MEIPASS
    5. Directory of __file__ and its parent folders (for development check)
    6. System PATH (via shutil.which)
    """
    candidate_dirs: list[Path] = []

    # 1 & 2. Next to sys.executable and bundled-tools
    if getattr(sys, "executable", None):
        exe_dir = Path(sys.executable).resolve().parent
        candidate_dirs.append(exe_dir)
        candidate_dirs.append(exe_dir / "bundled-tools")

    # 3 & 4. PyInstaller unpack root if present
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_dir = Path(meipass).resolve()
        candidate_dirs.append(meipass_dir)
        candidate_dirs.append(meipass_dir / "bundled-tools")

    suffixes = [".exe", ".cmd", ".bat", ""] if sys.platform == "win32" else [""]

    for d in candidate_dirs:
        for sfx in suffixes:
            target = d / f"{name}{sfx}"
            if target.is_file():
                return str(target)

    # 6. Fall back to standard PATH
    return shutil.which(name)


def get_ffmpeg_path() -> str | None:
    """Return the absolute path of the FFmpeg executable if found next to app or in PATH."""
    return _find_binary("ffmpeg")


def check_ffmpeg() -> tuple[bool, str | None]:
    """Check if FFmpeg is available and return (is_available, version_string_or_error).

    Returns:
        (True, version_str) if ffmpeg is found and runs successfully.
        (False, None) if ffmpeg executable is not found in PATH or bundled dir.
        (False, error_msg) if ffmpeg executable failed to execute.
    """
    path = get_ffmpeg_path()
    if not path:
        return False, None

    try:
        res = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout:
            # First line usually: "ffmpeg version 7.0.2 ... "
            first_line = res.stdout.splitlines()[0].strip()
            match = re.search(r"version\s+([^\s]+)", first_line)
            version = match.group(1) if match else first_line
            return True, version
        return False, f"FFmpeg returned exit code {res.returncode}"
    except Exception as exc:
        return False, str(exc)


def get_ffmpeg_install_hint() -> str:
    """Return platform-specific install instructions for FFmpeg."""
    if sys.platform == "win32":
        return "Install on Windows: `winget install Gyan.FFmpeg` or download from https://gyan.dev/ffmpeg/builds/"
    elif sys.platform == "darwin":
        return "Install on macOS: `brew install ffmpeg`"
    else:
        return "Install on Linux: `sudo apt update && sudo apt install ffmpeg` (or your distro's package manager)"


def require_ffmpeg(action: str = "merging audio/video or converting formats") -> str:
    """Ensure FFmpeg is available, raising FFmpegNotFoundError with installation hints if not.

    Returns:
        The path to the FFmpeg executable if found.
    """
    path = get_ffmpeg_path()
    if not path:
        hint = get_ffmpeg_install_hint()
        raise FFmpegNotFoundError(
            f"FFmpeg is required for {action}, but was not found in PATH.\n{hint}"
        )
    return path


def get_js_runtime() -> tuple[str, str] | None:
    """Check for a supported JavaScript runtime (deno, node, bun) next to app or in PATH.

    Returns:
        (name, path) tuple if found, None otherwise.
    """
    for runtime in ("deno", "node", "bun"):
        found = _find_binary(runtime)
        if found:
            return runtime, found
    return None


def get_js_runtime_install_hint() -> str:
    """Return platform-specific install instructions for a JavaScript runtime (Deno)."""
    if sys.platform == "win32":
        return "Install Deno: `winget install DenoLand.Deno` and restart your terminal."
    elif sys.platform == "darwin":
        return "Install Deno: `brew install deno` and restart your terminal."
    else:
        return "Install Deno: `curl -fsSL https://deno.land/install.sh | sh` or use your package manager, then restart your terminal."
