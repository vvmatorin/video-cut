from __future__ import annotations

import json
import shutil
from pathlib import Path

from .formatting import format_timestamp_hhmmss

_COLOR_FLAGS = (
    ("color_range", "-color_range", "tv"),
    ("color_space", "-colorspace", "bt709"),
    ("color_transfer", "-color_trc", "bt709"),
    ("color_primaries", "-color_primaries", "bt709"),
)


def build_vf_filters(
    *,
    left_pct: int,
    right_pct: int,
    top_pct: int,
    bottom_pct: int,
    override_fps: bool,
    output_fps: float,
    source_fps: float,
    add_hflip: bool,
    color_info: dict[str, str],
) -> str | None:
    filters: list[str] = []
    left = left_pct / 100.0
    right = right_pct / 100.0
    top = top_pct / 100.0
    bottom = bottom_pct / 100.0
    if any(v > 0 for v in (left, right, top, bottom)):
        crop_w = 1.0 - left - right
        crop_h = 1.0 - top - bottom
        filters.append(
            "crop="
            f"iw*{crop_w:.8f}:"
            f"ih*{crop_h:.8f}:"
            f"iw*{left:.8f}:"
            f"ih*{top:.8f}"
        )
    if override_fps:
        if source_fps >= output_fps:
            filters.append(f"fps={output_fps:g}")
        else:
            filters.append(f"setpts=N/({output_fps:g}*TB)")
    if add_hflip:
        filters.append("hflip")
    if not filters:
        return None
    color_range = color_info.get("color_range", "tv")
    if color_range not in ("pc", "tv"):
        color_range = "tv"
    filters.append(
        f"scale=in_range={color_range}:out_range={color_range}"
        ":flags=accurate_rnd+full_chroma_int"
    )
    return ",".join(filters)


def build_color_flags(color_info: dict[str, str]) -> list[str]:
    flags: list[str] = []
    for key, flag, default in _COLOR_FLAGS:
        flags += [flag, color_info.get(key) or default]
    return flags


def build_metadata_args(
    *,
    input_name: str,
    start_sec: float,
    duration_sec: float,
    target_frames: int,
    crop_left: int,
    crop_right: int,
    crop_top: int,
    crop_bottom: int,
    output_fps: float,
    include_audio: bool,
    add_hflip: bool,
) -> list[str]:
    payload = {
        "tool": "video_cut",
        "source_filename": input_name,
        "start_seconds": round(start_sec, 6),
        "start_timestamp": format_timestamp_hhmmss(start_sec),
        "clip_length_seconds": round(duration_sec, 6),
        "target_frames": target_frames,
        "crop_percent": {
            "left": crop_left,
            "right": crop_right,
            "top": crop_top,
            "bottom": crop_bottom,
        },
        "output_fps": round(output_fps, 6),
        "include_audio": include_audio,
        "horizontal_flip": add_hflip,
    }
    metadata_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return [
        "-metadata", f"comment={metadata_json}",
        "-metadata", f"description={metadata_json}",
        "-metadata", f"source_filename={input_name}",
        "-metadata", f"clip_start_seconds={start_sec:.6f}",
        "-metadata", f"clip_start_timestamp={format_timestamp_hhmmss(start_sec)}",
        "-metadata", f"clip_length_seconds={duration_sec:.6f}",
        "-metadata", f"target_frames={target_frames}",
        "-metadata", f"output_fps={output_fps:.6f}",
        "-metadata", f"crop_left_pct={crop_left}",
        "-metadata", f"crop_right_pct={crop_right}",
        "-metadata", f"crop_top_pct={crop_top}",
        "-metadata", f"crop_bottom_pct={crop_bottom}",
        "-metadata", f"horizontal_flip={1 if add_hflip else 0}",
        "-metadata", f"include_audio={1 if include_audio else 0}",
    ]


def build_export_command(
    *,
    input_path: Path,
    out_path: Path,
    start_sec: float,
    duration_sec: float,
    target_frames: int,
    left_pct: int,
    right_pct: int,
    top_pct: int,
    bottom_pct: int,
    override_fps: bool,
    output_fps: float,
    source_fps: float,
    include_audio: bool,
    add_hflip: bool,
    color_info: dict[str, str],
) -> list[str]:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg not found. Install ffmpeg (e.g. `brew install ffmpeg`).")

    vf = build_vf_filters(
        left_pct=left_pct,
        right_pct=right_pct,
        top_pct=top_pct,
        bottom_pct=bottom_pct,
        override_fps=override_fps,
        output_fps=output_fps,
        source_fps=source_fps,
        add_hflip=add_hflip,
        color_info=color_info,
    )
    coarse_seek_sec = max(0.0, start_sec - 1.0)
    fine_seek_sec = start_sec - coarse_seek_sec

    cmd = [
        ffmpeg_bin, "-hide_banner", "-y",
        "-ss", f"{coarse_seek_sec:.6f}",
        "-i", str(input_path),
        "-ss", f"{fine_seek_sec:.6f}",
        "-t", f"{duration_sec:.6f}",
    ]
    if vf:
        cmd += [
            "-vf", vf,
            "-c:v", "libx264",
            "-crf", "15",
            "-preset", "slow",
            "-profile:v", "high444",
            "-bf", "0",
            "-pix_fmt", "yuv444p",
        ]
        cmd += build_color_flags(color_info)
    else:
        cmd += ["-c:v", "copy"]

    cmd += ["-map_metadata", "0"]
    if include_audio:
        cmd += ["-c:a", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        cmd += ["-an"]

    cmd += build_metadata_args(
        input_name=input_path.name,
        start_sec=start_sec,
        duration_sec=duration_sec,
        target_frames=target_frames,
        crop_left=left_pct,
        crop_right=right_pct,
        crop_top=top_pct,
        crop_bottom=bottom_pct,
        output_fps=output_fps,
        include_audio=include_audio,
        add_hflip=add_hflip,
    )
    cmd += ["-frames:v", str(target_frames)]
    cmd += ["-sn", "-movflags", "+faststart", str(out_path)]
    return cmd
