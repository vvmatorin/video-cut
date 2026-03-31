# video-cut

A desktop GUI tool for precise video clip extraction. Scrub through a video frame-by-frame, set a start point, configure cropping and FPS, and export trimmed clips with a single click.

Built with PySide6 (Qt) and [libmpv](https://mpv.io/) for low-latency video playback, and FFmpeg for high-quality encoding.

## Features

- **Frame-accurate navigation** — step forward/backward one frame at a time, or scrub with the timeline slider
- **Visual crop preview** — adjust crop percentages per side and see the result overlaid on the video in real time
- **FPS control** — override the output frame rate or keep the source FPS; the clip duration auto-adjusts to hit your target frame count
- **Target frame count** — specify exactly how many frames the exported clip should contain
- **Horizontal flip** — optionally export a mirrored copy alongside the original
- **Audio passthrough** — include or strip the audio track
- **Background export** — exports run in background threads; you can queue multiple clips without waiting
- **Rich metadata** — export parameters (crop, start time, FPS, frame count) are embedded into the output file's metadata for reproducibility
- **Color-aware encoding** — source color space, transfer, primaries, and range are detected via `ffprobe` and preserved in the output

## Quick Start (MacOS)

The included install script handles Homebrew, mpv, FFmpeg, uv, and Python dependencies automatically:

```bash
./install.sh
```

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Left` / `Right` | Step one frame backward / forward |
| `Space` | Play / Pause |
| `Cmd+O` | Open video file |
| `Ctrl+Shift+S` | Set start point at current position |
| `Ctrl+G` | Jump to start point |
| `Ctrl+P` | Play clip preview |
| `Ctrl+E` | Export clip |

## Project Structure

```
video-cut/
├── main.py              # Application entry point and main window
├── utils/
│   ├── ffmpeg.py        # FFmpeg command builder (filters, color, metadata)
│   ├── formatting.py    # Time formatting utilities
│   ├── widgets.py       # mpv OpenGL widget and crop overlay
│   └── workers.py       # Background threads for export and ffprobe
├── install.sh           # One-command macOS setup script
├── pyproject.toml       # Project metadata and dependencies
└── uv.lock              # Locked dependency versions
```
