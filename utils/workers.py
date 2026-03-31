from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, Signal

_CANCEL_POLL_SEC = 0.1


def _wait_or_cancel(
    proc: subprocess.Popen[str], thread: QThread
) -> tuple[str | None, str | None]:
    """Block until *proc* finishes, checking *thread* for cancellation.

    Uses ``communicate(timeout=...)`` so pipe buffers are drained safely,
    avoiding the deadlock that ``poll()`` + ``sleep()`` can cause when
    stderr/stdout fills the OS pipe buffer.

    Returns ``(stdout, stderr)`` on normal exit, ``(None, None)`` if cancelled.
    """
    while True:
        try:
            return proc.communicate(timeout=_CANCEL_POLL_SEC)
        except subprocess.TimeoutExpired:
            if thread.isInterruptionRequested():
                proc.terminate()
                try:
                    proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                return None, None


class ExportWorker(QThread):
    finished_ok = Signal(str)
    finished_error = Signal(str)

    def __init__(self, commands: list[list[str]], job_name: str) -> None:
        super().__init__()
        self.commands = commands
        self.job_name = job_name
        self._proc: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self.requestInterruption()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    def run(self) -> None:
        for cmd in self.commands:
            if self.isInterruptionRequested():
                self.finished_error.emit(f"Export cancelled: {self.job_name}")
                return
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            stdout, stderr = _wait_or_cancel(self._proc, self)
            if stdout is None:
                self.finished_error.emit(f"Export cancelled: {self.job_name}")
                return
            if self._proc.returncode != 0:
                error_output = (stderr or stdout or "").strip()
                self.finished_error.emit(
                    f"Command failed:\n{shlex.join(cmd)}\n\n{error_output}"
                )
                return
        self._proc = None
        self.finished_ok.emit(f"Export completed: {self.job_name}")


class ProbeWorker(QThread):
    metadata_ready = Signal(str, float, float, int, int, object)
    metadata_error = Signal(str, str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self._proc: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self.requestInterruption()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            ffprobe = shutil.which("ffprobe")
            if not ffprobe:
                raise RuntimeError(
                    "ffprobe not found. Install ffmpeg (e.g. `brew install ffmpeg`)."
                )
            cmd = [
                ffprobe, "-v", "error",
                "-show_entries",
                "format=duration:stream=avg_frame_rate,width,height,"
                "color_range,color_space,color_transfer,color_primaries",
                "-select_streams", "v:0",
                "-of", "json",
                str(self.path),
            ]
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            stdout, stderr = _wait_or_cancel(self._proc, self)
            if stdout is None:
                return
            if self._proc.returncode != 0:
                raise RuntimeError((stderr or stdout or "ffprobe failed").strip())

            data = json.loads(stdout)
            stream = (data.get("streams") or [{}])[0]

            duration = float(data.get("format", {}).get("duration", 0.0))
            width = int(stream.get("width", 0) or 0)
            height = int(stream.get("height", 0) or 0)

            fps = 30.0
            rate = stream.get("avg_frame_rate", "0/1")
            num, den = rate.split("/")
            if float(den) != 0:
                fps = float(num) / float(den)

            color_keys = ("color_range", "color_space", "color_transfer", "color_primaries")
            color_info = {
                k: v for k in color_keys if (v := stream.get(k)) and v != "unknown"
            }

            if not self.isInterruptionRequested():
                self.metadata_ready.emit(
                    str(self.path), duration, max(1.0, fps), width, height, color_info
                )
        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.metadata_error.emit(str(self.path), str(exc))
