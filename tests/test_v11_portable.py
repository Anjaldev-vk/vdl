"""Unit tests for V11: Standalone Portable Distribution binary detection."""

import sys
from pathlib import Path
from unittest.mock import patch

from vdl.doctor import check_python
from vdl.media import _find_binary, get_js_runtime


def test_find_binary_in_bundled_tools(tmp_path):
    """_find_binary finds binaries placed in bundled-tools subdirectory next to sys.executable."""
    bundled_dir = tmp_path / "bundled-tools"
    bundled_dir.mkdir()
    fake_ffmpeg = bundled_dir / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    fake_ffmpeg.write_text("fake binary")

    fake_exe = tmp_path / ("vdl.exe" if sys.platform == "win32" else "vdl")
    fake_exe.write_text("fake exe")

    with patch.object(sys, "executable", str(fake_exe)):
        found = _find_binary("ffmpeg")
        assert found is not None
        assert Path(found).resolve() == fake_ffmpeg.resolve()


def test_find_binary_next_to_executable(tmp_path):
    """_find_binary finds binaries placed directly next to sys.executable."""
    fake_deno = tmp_path / ("deno.exe" if sys.platform == "win32" else "deno")
    fake_deno.write_text("fake deno")

    fake_exe = tmp_path / ("vdl.exe" if sys.platform == "win32" else "vdl")
    fake_exe.write_text("fake exe")

    with patch.object(sys, "executable", str(fake_exe)):
        found = _find_binary("deno")
        assert found is not None
        assert Path(found).resolve() == fake_deno.resolve()


def test_find_binary_via_meipass(tmp_path):
    """_find_binary finds binaries when sys._MEIPASS is set (onefile or PyInstaller temp)."""
    fake_ffmpeg = tmp_path / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    fake_ffmpeg.write_text("fake binary")

    with patch.object(sys, "_MEIPASS", str(tmp_path), create=True):
        with patch.object(sys, "executable", "/nonexistent/path"):
            found = _find_binary("ffmpeg")
            assert found is not None
            assert Path(found).resolve() == fake_ffmpeg.resolve()


def test_get_js_runtime_detects_bundled_deno(tmp_path):
    """get_js_runtime successfully returns ('deno', path) when deno is bundled next to app."""
    fake_deno = tmp_path / ("deno.exe" if sys.platform == "win32" else "deno")
    fake_deno.write_text("fake deno")
    fake_exe = tmp_path / "vdl.exe"

    with patch.object(sys, "executable", str(fake_exe)):
        res = get_js_runtime()
        assert res is not None
        runtime, path = res
        assert runtime == "deno"
        assert Path(path).resolve() == fake_deno.resolve()


def test_doctor_check_python_frozen_support():
    """check_python passes when sys.version satisfies requires-python even if pyproject is absent."""
    with patch("vdl.doctor.get_required_python_version", return_value=(3, 13)):
        res = check_python()
        assert res.status.value == "OK"
