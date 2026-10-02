"""Diagnostic checks for system dependencies, environment, and configuration."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import tomllib
import uuid
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import metadata
from pathlib import Path

import yt_dlp
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from vdl.config import Settings, load_settings
from vdl.media import _find_binary, get_ffmpeg_path
from vdl.network import is_proxy_reachable

logger = logging.getLogger(__name__)


def get_required_python_version() -> tuple[int, int]:
    """Dynamically determine required Python version from pyproject.toml or package metadata."""
    req: str | None = None
    pyproject_path = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            req = data.get("project", {}).get("requires-python")
        except Exception:
            pass
    if not req:
        try:
            meta = metadata("vdl")
            req = meta.get("Requires-Python")
        except Exception:
            pass

    if req:
        match = re.search(r">=(\d+)\.(\d+)", req)
        if match:
            return int(match.group(1)), int(match.group(2))
    return (3, 13)


class CheckStatus(StrEnum):
    """Status badge for doctor checks."""

    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    """Individual diagnostic check outcome."""

    name: str
    status: CheckStatus
    detail: str
    fix_hint: str | None = None

    @property
    def badge(self) -> str:
        """Formatted Rich markup badge."""
        if self.status == CheckStatus.OK:
            return "[bold green]OK[/bold green]"
        if self.status == CheckStatus.WARN:
            return "[bold yellow]WARN[/bold yellow]"
        return "[bold red]FAIL[/bold red]"


def _run_quick_cmd(cmd: list[str]) -> str | None:
    """Run a quick version query with a short timeout."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.strip().splitlines()[0].strip()
    except Exception as exc:
        logger.debug("Failed running command %s: %s", cmd, exc)
    return None


def check_python() -> CheckResult:
    """Verify running Python runtime satisfies requires-python from pyproject.toml."""
    min_major, min_minor = get_required_python_version()
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    is_supported = (sys.version_info.major, sys.version_info.minor) >= (min_major, min_minor)
    if is_supported:
        return CheckResult(
            name="Python",
            status=CheckStatus.OK,
            detail=f"v{py_ver} (requires >={min_major}.{min_minor})",
        )
    return CheckResult(
        name="Python",
        status=CheckStatus.FAIL,
        detail=f"v{py_ver} (requires >={min_major}.{min_minor})",
        fix_hint=f"Upgrade to Python {min_major}.{min_minor} or newer.",
    )


def check_ytdlp() -> CheckResult:
    """Verify yt-dlp library version."""
    ytdlp_ver = getattr(yt_dlp.version, "__version__", "unknown")
    return CheckResult(
        name="yt-dlp",
        status=CheckStatus.OK,
        detail=f"v{ytdlp_ver}",
    )


def check_ffmpeg() -> CheckResult:
    """Verify FFmpeg binary presence and version."""
    ffmpeg_path = get_ffmpeg_path()
    if not ffmpeg_path:
        return CheckResult(
            name="FFmpeg",
            status=CheckStatus.FAIL,
            detail="Not found in PATH",
            fix_hint="Install FFmpeg (winget install Gyan.FFmpeg / brew install ffmpeg) and restart terminal.",
        )

    ver_line = _run_quick_cmd([ffmpeg_path, "-version"])
    detail = ver_line.split("Copyright")[0].strip() if ver_line else "Available"
    return CheckResult(
        name="FFmpeg",
        status=CheckStatus.OK,
        detail=detail,
    )


def check_js_runtime() -> CheckResult:
    """Verify JavaScript runtime presence (Deno = OK; Node/Bun alone = WARN)."""
    # 1. Prefer Deno (full pass)
    deno_path = _find_binary("deno")
    if deno_path:
        ver = _run_quick_cmd([deno_path, "--version"])
        detail = ver if ver else "Deno available"
        return CheckResult(
            name="JS Runtime",
            status=CheckStatus.OK,
            detail=f"{detail} [recommended]",
        )

    # 2. Node.js (WARN with Deno install recommendation)
    node_path = _find_binary("node")
    if node_path:
        ver = _run_quick_cmd([node_path, "--version"])
        detail = f"Node.js ({ver})" if ver else "Node.js"
        return CheckResult(
            name="JS Runtime",
            status=CheckStatus.WARN,
            detail=detail,
            fix_hint="Deno is recommended for best yt-dlp compatibility: winget install DenoLand.Deno / brew install deno",
        )

    # 3. Bun (WARN with Deno install recommendation)
    bun_path = _find_binary("bun")
    if bun_path:
        ver = _run_quick_cmd([bun_path, "--version"])
        detail = f"Bun ({ver})" if ver else "Bun"
        return CheckResult(
            name="JS Runtime",
            status=CheckStatus.WARN,
            detail=detail,
            fix_hint="Deno is recommended for best yt-dlp compatibility: winget install DenoLand.Deno / brew install deno",
        )

    # 4. None found
    return CheckResult(
        name="JS Runtime",
        status=CheckStatus.WARN,
        detail="None found",
        fix_hint="Install Deno (winget install DenoLand.Deno / brew install deno) to support YouTube format extraction.",
    )


def check_tor_binary() -> CheckResult:
    """Verify Tor binary presence (optional)."""
    tor_path = shutil.which("tor")
    if tor_path:
        ver = _run_quick_cmd([tor_path, "--version"])
        detail = ver if ver else "Installed"
        return CheckResult(
            name="Tor Binary",
            status=CheckStatus.OK,
            detail=detail,
        )
    return CheckResult(
        name="Tor Binary",
        status=CheckStatus.WARN,
        detail="Not installed (optional)",
        fix_hint="Optional: install Tor daemon (winget install TorProject.Tor) or Tor Browser.",
    )


def check_tor_reachability(settings: Settings) -> CheckResult:
    """Verify Tor SOCKS5 proxy connectivity."""
    proxy_url = settings.tor_proxy
    reachable = is_proxy_reachable(proxy_url, timeout=1.5)
    if reachable:
        return CheckResult(
            name="Tor Proxy",
            status=CheckStatus.OK,
            detail=f"Reachable at {proxy_url}",
        )
    return CheckResult(
        name="Tor Proxy",
        status=CheckStatus.WARN,
        detail=f"Unreachable at {proxy_url} (optional)",
        fix_hint="Tor is optional. Tor Browser uses port 9150, Tor daemon uses port 9050.",
    )


def check_download_dir(settings: Settings) -> CheckResult:
    """Verify configured download directory exists and has write permissions."""
    dl_dir = settings.download_dir.expanduser()
    try:
        dl_dir.mkdir(parents=True, exist_ok=True)
        # Test write permission using a probe file
        probe_file = dl_dir / f".vdl_probe_{uuid.uuid4().hex}.tmp"
        probe_file.write_text("vdl probe", encoding="utf-8")
        probe_file.unlink()
        return CheckResult(
            name="Download Dir",
            status=CheckStatus.OK,
            detail=f"{dl_dir} (writable)",
        )
    except Exception as exc:
        return CheckResult(
            name="Download Dir",
            status=CheckStatus.FAIL,
            detail=f"{dl_dir} (write error: {exc})",
            fix_hint="Check directory permissions or set another path via 'vdl config set download_dir <path>'.",
        )


def run_doctor_checks(settings: Settings | None = None) -> list[CheckResult]:
    """Execute all diagnostic checks and return results list."""
    if settings is None:
        settings = load_settings()

    return [
        check_python(),
        check_ytdlp(),
        check_ffmpeg(),
        check_js_runtime(),
        check_tor_binary(),
        check_tor_reachability(settings),
        check_download_dir(settings),
    ]


def render_doctor_table(results: list[CheckResult], console: Console | None = None) -> None:
    """Render doctor check outcomes in a Rich Table."""
    out_console = console or Console()
    table = Table(title="vdl System Diagnostics (Doctor)", border_style="cyan")
    table.add_column("Component", style="bold cyan")
    table.add_column("Status", justify="center")
    table.add_column("Detail", style="white")
    table.add_column("Fix Hint", style="dim")

    for res in results:
        hint_text = escape(res.fix_hint or "")
        table.add_row(
            escape(res.name),
            res.badge,
            escape(res.detail),
            hint_text,
        )

    out_console.print(table)


def run_doctor(settings: Settings | None = None, console: Console | None = None) -> int:
    """Run all doctor checks, print results, and return appropriate exit code (0 OK/WARN, 1 FAIL)."""
    out_console = console or Console()
    results = run_doctor_checks(settings)
    render_doctor_table(results, out_console)

    has_fail = any(r.status == CheckStatus.FAIL for r in results)
    if has_fail:
        out_console.print(
            "\n[bold red]Doctor check failed: One or more critical dependencies are missing or misconfigured.[/bold red]"
        )
        return 1

    has_warn = any(r.status == CheckStatus.WARN for r in results)
    if has_warn:
        out_console.print(
            "\n[bold green]Doctor check passed with warnings:[/] All critical dependencies are satisfied."
        )
    else:
        out_console.print(
            "\n[bold green]Doctor check passed:[/] All components are in optimal condition."
        )
    return 0
