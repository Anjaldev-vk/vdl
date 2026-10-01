# vdl - Video Downloader CLI

A robust, production-quality terminal video and audio downloader powered by `yt-dlp`, `typer`, and `rich`.

> [!IMPORTANT]
> **LEGAL & AUTHORIZATION NOTICE**
> You must only download content you are authorized to download. Respect copyright laws, creators' intellectual property, and platform terms of service. `vdl` does not bypass DRM, harvest browser session cookies, or access private credentials.

---

## Features

- **Format Selection**: Nearest-resolution matching with automatic fallback (`1080p`, `720p`, `480p`, `best`), and codec preferences (`h264`, `av1`, `any`).
- **Audio Extraction**: Clean audio stream-copy (lossless extraction for AAC/M4A and Opus) or high-quality transcoding (MP3).
- **Interactive Rich UI**: Non-interfering progress bars, download speed, ETA, stream phase transitions, and escaped metadata display.
- **Privacy-Preserving Tor Mode**: Strict fail-closed SOCKS5 routing—aborts immediately if the Tor proxy is unreachable with zero IP leakage.
- **System Doctor**: Built-in environment and dependency diagnostics (`vdl doctor`) checking Python, yt-dlp, FFmpeg, Deno/Node, and Tor.
- **Persistent Configuration**: User preferences persisted in standard platform config directories (`%APPDATA%\vdl\config.json` on Windows, `~/.config/vdl/config.json` on Linux/macOS).
- **Diagnostics & Logging**: Rotating file handler capturing detailed events without cluttering the console.

---

## Prerequisites

1. **Python**: Version `3.13` or newer.
2. **FFmpeg**: Required for media stream merging and audio extraction.
   - **Windows**: `winget install Gyan.FFmpeg` or `choco install ffmpeg`
   - **macOS**: `brew install ffmpeg`
   - **Linux**: `sudo apt install ffmpeg` / `sudo dnf install ffmpeg`
3. **JavaScript Runtime (Deno)**: Recommended for YouTube format extraction.
   - **Windows**: `winget install DenoLand.Deno`
   - **macOS / Linux**: `brew install deno` or `curl -fsSL https://deno.land/install.sh | sh`

---

## Installation

### Using `uv` (Recommended)

Install directly as a standalone CLI tool:

```bash
uv tool install .
```

Or sync development dependencies locally:

```bash
uv sync
uv run vdl --help
```

---

## Quick Start & Command Reference

### 1. Download Video (`vdl download`)
Download video or playlists with quality and codec preferences.

```bash
# Download best available quality
vdl download "https://youtu.be/x7X9w_GIm1s"

# Download 1080p with AV1 codec preference to a custom directory
vdl download "https://youtu.be/x7X9w_GIm1s" -q 1080p --prefer av1 -o ./videos

# Download using Tor privacy mode
vdl download "https://youtu.be/x7X9w_GIm1s" --network tor
```

**Options:**
- `-o`, `--output`, `--output-dir`: Output destination directory.
- `-q`, `--quality`: Target video quality (`1080p`, `720p`, `480p`, `best`).
- `--format-id`: Exact format ID from `vdl formats`.
- `--prefer`: Preferred video codec (`h264`, `av1`, `any`).
- `--playlist`: Enable downloading full playlist.
- `--network`: Network mode (`direct` or `tor`).

---

### 2. Extract Audio (`vdl audio`)
Extract audio streams, copying streams losslessly whenever possible.

```bash
# Extract default audio (MP3)
vdl audio "https://youtu.be/x7X9w_GIm1s"

# Extract native Opus stream to a music directory
vdl audio "https://youtu.be/x7X9w_GIm1s" -f opus -o ./music

# Extract AAC audio (M4A)
vdl audio "https://youtu.be/x7X9w_GIm1s" -f m4a
```

**Options:**
- `-f`, `--format`: Audio target format (`mp3`, `m4a`, `opus`, `best`).
- `-o`, `--output`, `--output-dir`: Output destination directory.
- `--network`: Network mode (`direct` or `tor`).

---

### 3. Video Metadata (`vdl info`)
Inspect video details, duration, resolutions, and filesize estimates without downloading.

```bash
vdl info "https://youtu.be/x7X9w_GIm1s"
```

---

### 4. Available Formats Table (`vdl formats`)
List available streams, codecs, bitrates, and file containers.

```bash
# Show video and audio formats (excluding storyboards)
vdl formats "https://youtu.be/x7X9w_GIm1s"

# Include storyboard preview formats
vdl formats "https://youtu.be/x7X9w_GIm1s" --all
```

---

### 5. Diagnostics (`vdl doctor`)
Run health checks on your runtime environment, FFmpeg installation, JS runtime, and proxy connectivity.

```bash
vdl doctor
```

---

### 6. Configuration Management (`vdl config`)
Manage persistent configuration options across CLI invocations.

```bash
# View active configuration and file location
vdl config show

# Set default download directory
vdl config set download_dir ~/Downloads/Media

# Set default quality
vdl config set default_quality 1080p

# Set default network mode to Tor
vdl config set network_mode tor

# Reset all settings to factory defaults
vdl config reset

# Print configuration file path
vdl config path
```

---

### 7. Version (`vdl version` / `vdl -V`)
Print the current version of `vdl`:

```bash
vdl version
vdl -V
```

---

## Tor Setup Notes

`vdl` routes traffic over SOCKS5h in Tor mode. It features strict **fail-closed** protection: if the SOCKS5 proxy is unreachable, execution halts immediately with a `NetworkError` to guarantee no accidental IP exposure.

- **Tor Daemon (Standard)**: Defaults to port `9050` (`socks5h://127.0.0.1:9050`).
- **Tor Browser**: When running the Tor Browser bundle, the SOCKS proxy listens on port `9150`. If you use Tor Browser as your proxy, configure `vdl`:
  ```bash
  vdl config set tor_proxy socks5h://127.0.0.1:9150
  ```

---

## Troubleshooting

- **`FFmpeg not found`**:
  Make sure FFmpeg is installed and added to your system `PATH`. Restart your terminal after installing. Run `vdl doctor` to verify detection.
- **`Requested format is not available / No JavaScript runtime`**:
  YouTube frequently requires a JavaScript runtime to extract cipher formats. Install Deno (`winget install DenoLand.Deno` / `brew install deno`) and verify with `vdl doctor`.
- **`Tor proxy unreachable / fail closed`**:
  Ensure your Tor daemon is running or Tor Browser is open. Check the configured port using `vdl config show`.
- **`Unsupported URL`**:
  Verify the URL. `vdl` delegates to `yt-dlp`'s library of extractors. If the site is unsupported or private, `vdl` exits cleanly with a descriptive message.

---

## Architecture Overview

- **`vdl.cli`**: Typer command-line interface, subcommands, flag definitions, help messages, exit codes, and logger initialization.
- **`vdl.config`**: Dataclass-based settings management with atomic JSON persistence, platform config directory resolution, and validation.
- **`vdl.display`**: Rich terminal interface handling escaped markup, custom progress bar columns, format tables, metadata panels, and colored status alerts.
- **`vdl.doctor`**: System diagnostic suite checking Python version, yt-dlp version, FFmpeg binaries, JavaScript runtimes, Tor connectivity, and disk permissions.
- **`vdl.downloader`**: High-level wrapper around `yt-dlp` implementing format selection, stream copy logic, and translating raw exceptions into domain errors.
- **`vdl.exceptions`**: Domain exception hierarchy (`VdlError`, `DownloadError`, `GeoRestrictedError`, `LoginRequiredError`, `NetworkError`, etc.).
- **`vdl.media`**: Binary discovery utilities for external tools like FFmpeg, duration formatters, and byte size formatting.
- **`vdl.network`**: Proxy URL parsing, socket reachability testing, and centralized fail-closed Tor mode enforcement.
