from __future__ import annotations

import locale
import sys
from pathlib import Path

import mpv

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from utils import ffmpeg
from utils.formatting import format_seconds, format_timestamp_filename
from utils.widgets import MpvWidget
from utils.workers import ExportWorker, ProbeWorker


class VideoPrepWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Video Training Preprocessor")
        self.resize(1200, 760)

        self.input_path: Path | None = None
        self.duration_seconds = 0.0
        self.source_fps = 30.0
        self.source_width = 0
        self.source_height = 0
        self.current_position_ms = 0
        self.start_position_ms = 0
        self.user_is_scrubbing = False
        self.was_playing_before_scrub = False
        self.export_workers: list[ExportWorker] = []
        self.active_export_paths: set[str] = set()
        self.probe_worker: ProbeWorker | None = None
        self.is_closing = False
        self.source_color_info: dict[str, str] = {}
        self._duration_set = False

        # Cached mpv state — written by observer callbacks (mpv thread),
        # read by the UI-update timer (main thread).  Simple attribute
        # assignments are atomic under CPython's GIL.
        self._mpv_time_pos: float | None = None
        self._mpv_duration: float | None = None
        self._mpv_pause: bool = True
        self._mpv_vid_w: int | None = None
        self._mpv_vid_h: int | None = None

        self._build_ui()

        self.player: mpv.MPV | None = None

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(30)
        self._poll_timer.timeout.connect(self._poll_mpv_state)

        self.clip_play_timer = QTimer(self)
        self.clip_play_timer.setSingleShot(True)
        self.clip_play_timer.timeout.connect(self._stop_clip_playback)

        self._connect_signals()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.player is None:
            self._init_mpv()

    def _init_mpv(self) -> None:
        locale.setlocale(locale.LC_NUMERIC, "C")
        self.player = mpv.MPV(
            vo="libmpv",
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
            keep_open="always",
            idle="yes",
            pause=True,
            mute=True,
        )
        self._register_mpv_observers()
        self.video_widget.init_render_context(self.player)

    # ------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QGridLayout(root)
        layout.setColumnStretch(0, 6)
        layout.setColumnStretch(1, 1)

        controls = QVBoxLayout()
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 1)
        self.timeline.setValue(0)
        self.timeline.setEnabled(False)
        time_row = QHBoxLayout()
        self.position_range_lbl = QLabel("00:00 -> 00:00")
        time_row.addWidget(self.position_range_lbl)
        controls.addWidget(self.timeline)
        controls.addLayout(time_row)

        self.video_widget = MpvWidget()

        left_panel = QVBoxLayout()
        left_panel.addWidget(self.video_widget, stretch=1)
        left_panel.addLayout(controls)

        settings_box = QGroupBox()
        settings_box.setMaximumWidth(320)
        form = QFormLayout(settings_box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.left_crop = QSpinBox()
        self.right_crop = QSpinBox()
        self.top_crop = QSpinBox()
        self.bottom_crop = QSpinBox()
        for s in (self.left_crop, self.right_crop, self.top_crop, self.bottom_crop):
            s.setRange(0, 95)
            s.setSingleStep(1)
            s.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.target_frames = QSpinBox()
        self.target_frames.setRange(1, 1000)
        self.target_frames.setSingleStep(1)
        self.target_frames.setValue(150)
        self.target_frames.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.override_fps = QCheckBox("Override FPS")
        self.override_fps.setChecked(True)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 120)
        self.fps_spin.setValue(30)
        self.fps_spin.setEnabled(True)
        self.fps_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.include_audio = QCheckBox("Include audio track")
        self.include_audio.setChecked(True)
        self.preview_audio = QCheckBox("Preview audio in player")
        self.preview_audio.setChecked(False)
        self.save_hflip = QCheckBox("Also save horizontal flip copy")
        self.save_hflip.setChecked(False)

        self.output_name = QLineEdit("clip")
        self.output_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_start_btn = QPushButton("Set Start Here")
        self.go_start_btn = QPushButton("Go To Start")
        self.play_clip_btn = QPushButton("Play Clip")
        self.start_lbl = QLabel("Start: 00:00")
        self.open_btn = QPushButton("Open MP4")
        self.export_btn = QPushButton("Export")
        self.export_status = QLabel("Ready.")
        self.export_status.setMaximumWidth(320)
        self.export_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._set_export_status("Ready.")

        form.addRow("Crop left (%)", self.left_crop)
        form.addRow("Crop right (%)", self.right_crop)
        form.addRow("Crop top (%)", self.top_crop)
        form.addRow("Crop bottom (%)", self.bottom_crop)
        form.addRow("Target frames", self.target_frames)
        form.addRow(self.override_fps, self.fps_spin)
        form.addRow(self.preview_audio)
        form.addRow(self.include_audio)
        form.addRow(self.save_hflip)
        form.addRow("Output name", self.output_name)

        info_box = QGroupBox("Source Info")
        info_box.setMaximumWidth(320)
        info_form = QFormLayout(info_box)
        info_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        info_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.source_resolution_lbl = QLabel("—")
        self.source_fps_lbl = QLabel("—")
        info_form.addRow("Resolution", self.source_resolution_lbl)
        info_form.addRow("FPS", self.source_fps_lbl)

        right_panel = QVBoxLayout()
        start_row = QHBoxLayout()
        start_row.addWidget(self.set_start_btn)
        start_row.addWidget(self.go_start_btn)

        clip_row = QHBoxLayout()
        clip_row.addWidget(self.play_clip_btn)

        right_panel.addWidget(info_box)
        right_panel.addWidget(settings_box)
        right_panel.addLayout(start_row)
        right_panel.addWidget(self.start_lbl)
        right_panel.addLayout(clip_row)
        right_panel.addStretch()
        right_panel.addWidget(self.open_btn)
        right_panel.addWidget(self.export_btn)
        right_panel.addWidget(self.export_status)

        layout.addLayout(left_panel, 0, 0)
        layout.addLayout(right_panel, 0, 1)

    def _add_shortcut(self, key, handler) -> None:
        action = QAction(self)
        action.setShortcut(key)
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.triggered.connect(handler)
        self.addAction(action)

    def _connect_signals(self) -> None:
        self.open_btn.clicked.connect(self.open_video)
        self.export_btn.clicked.connect(self.export_clip)
        self.set_start_btn.clicked.connect(self.set_start_here)
        self.go_start_btn.clicked.connect(self.go_to_start)
        self.play_clip_btn.clicked.connect(self.play_clip)

        self.timeline.sliderPressed.connect(self.on_slider_pressed)
        self.timeline.sliderReleased.connect(self.on_slider_released)
        self.timeline.sliderMoved.connect(self.on_slider_moved)

        self.override_fps.toggled.connect(self.fps_spin.setEnabled)
        self.override_fps.toggled.connect(lambda _: self.update_position_display())
        self.preview_audio.toggled.connect(self.on_preview_audio_toggled)
        self.target_frames.valueChanged.connect(self.update_position_display)
        self.fps_spin.valueChanged.connect(self.update_position_display)

        for crop in (self.left_crop, self.right_crop, self.top_crop, self.bottom_crop):
            crop.valueChanged.connect(self.update_overlay)

        for editor in (
            self.left_crop,
            self.right_crop,
            self.top_crop,
            self.bottom_crop,
            self.target_frames,
            self.fps_spin,
            self.output_name,
        ):
            editor.editingFinished.connect(self.release_input_focus)
        self.video_widget.clicked.connect(self.release_input_focus)

        self._add_shortcut(Qt.Key.Key_Left, lambda: self.step_frame(-1))
        self._add_shortcut(Qt.Key.Key_Right, lambda: self.step_frame(1))
        self._add_shortcut(Qt.Key.Key_Space, self.toggle_play_pause)
        self._add_shortcut(QKeySequence.StandardKey.Open, self.open_video)
        self._add_shortcut(QKeySequence("Ctrl+E"), self.export_clip)
        self._add_shortcut(QKeySequence("Ctrl+Shift+S"), self.set_start_here)
        self._add_shortcut(QKeySequence("Ctrl+G"), self.go_to_start)
        self._add_shortcut(QKeySequence("Ctrl+P"), self.play_clip)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    # ------------------------------------------------------------------ display

    def _set_export_status(self, text: str) -> None:
        width = self.export_status.width()
        if width <= 16:
            width = 300
        elided = self.export_status.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, width
        )
        self.export_status.setText(elided)
        self.export_status.setToolTip(text)

    def update_start_display(self) -> None:
        self.start_lbl.setText(f"Start: {format_seconds(self.start_position_ms / 1000.0)}")

    def update_position_display(self, preview_position_ms: int | None = None) -> None:
        current_ms = self.timeline.value() if self.user_is_scrubbing else self.current_position_ms
        if preview_position_ms is not None:
            current_ms = preview_position_ms
        max_ms = self.timeline.maximum()
        clip_end_ms = current_ms + int(self._source_clip_duration_sec() * 1000)
        if max_ms > 0:
            clip_end_ms = min(clip_end_ms, max_ms)
        self.position_range_lbl.setText(
            f"{format_seconds(current_ms / 1000.0)} -> {format_seconds(clip_end_ms / 1000.0)}"
        )

    def update_overlay(self) -> None:
        self.video_widget.set_crop(
            self.left_crop.value(),
            self.right_crop.value(),
            self.top_crop.value(),
            self.bottom_crop.value(),
        )

    # --------------------------------------------------------- mpv observers

    def _register_mpv_observers(self) -> None:
        """Register async property observers so we never call
        mpv_get_property from the Qt main thread (avoids macOS deadlock
        between the mpv render thread and the Cocoa event loop)."""

        @self.player.property_observer("time-pos")
        def _on_time_pos(_name: str, value: float | None) -> None:
            self._mpv_time_pos = value

        @self.player.property_observer("duration")
        def _on_duration(_name: str, value: float | None) -> None:
            self._mpv_duration = value

        @self.player.property_observer("pause")
        def _on_pause(_name: str, value: bool | None) -> None:
            if value is not None:
                self._mpv_pause = value

        @self.player.property_observer("width")
        def _on_width(_name: str, value: int | None) -> None:
            self._mpv_vid_w = value

        @self.player.property_observer("height")
        def _on_height(_name: str, value: int | None) -> None:
            self._mpv_vid_h = value

    # ---------------------------------------------------------------- playback

    def _is_playing(self) -> bool:
        return not self._mpv_pause

    def toggle_play_pause(self) -> None:
        if self.input_path is None or self.player is None:
            return
        if self._is_playing():
            self.clip_play_timer.stop()
            self.player.pause = True
        else:
            self.player.pause = False

    def play_clip(self) -> None:
        if self.input_path is None or self.player is None:
            return
        self.clip_play_timer.stop()
        self.player.seek(self.start_position_ms / 1000.0, "absolute+exact")
        self.player.pause = False
        self.clip_play_timer.start(int(self._source_clip_duration_sec() * 1000))

    def _stop_clip_playback(self) -> None:
        if self.player is not None:
            self.player.pause = True

    def on_preview_audio_toggled(self, enabled: bool) -> None:
        if self.player is not None:
            self.player.mute = not enabled

    def set_start_here(self) -> None:
        if self.input_path is None:
            return
        self.start_position_ms = self.current_position_ms
        self.update_start_display()

    def go_to_start(self) -> None:
        if self.input_path is None or self.player is None:
            return
        self.player.seek(self.start_position_ms / 1000.0, "absolute+exact")

    def step_frame(self, direction: int) -> None:
        if self.input_path is None or self.player is None:
            return
        if self._is_playing():
            self.player.pause = True
        if direction > 0:
            self.player.frame_step()
        else:
            self.player.frame_back_step()

    def release_input_focus(self) -> None:
        focused = QApplication.focusWidget()
        if isinstance(focused, (QAbstractSpinBox, QLineEdit)):
            focused.clearFocus()
        self.video_widget.setFocus()

    # ------------------------------------------------------ mpv state polling

    def _poll_mpv_state(self) -> None:
        """Read cached observer values and update the UI.
        No synchronous mpv calls are made here."""
        duration = self._mpv_duration
        time_pos = self._mpv_time_pos

        if duration is not None and duration > 0:
            dur_ms = int(duration * 1000)
            if not self._duration_set or self.timeline.maximum() != dur_ms:
                self._duration_set = True
                self.duration_seconds = duration
                self.timeline.setRange(0, dur_ms)
                self.timeline.setEnabled(True)
                self.update_position_display()

                vw = self._mpv_vid_w
                vh = self._mpv_vid_h
                if vw and vh:
                    self.video_widget.set_video_size(vw, vh)

        if time_pos is not None:
            pos_ms = int(time_pos * 1000)
            if pos_ms != self.current_position_ms:
                self.current_position_ms = pos_ms
                if not self.user_is_scrubbing:
                    self.timeline.setValue(pos_ms)
                self.update_position_display(pos_ms)

    # --------------------------------------------------------- slider / scrub

    def on_slider_pressed(self) -> None:
        self.user_is_scrubbing = True
        self.was_playing_before_scrub = self._is_playing()
        if self.was_playing_before_scrub and self.player is not None:
            self.player.pause = True

    def on_slider_released(self) -> None:
        self.user_is_scrubbing = False
        if self.player is not None:
            self.player.seek(self.timeline.value() / 1000.0, "absolute+exact")
            if self.was_playing_before_scrub:
                self.player.pause = False
        self.update_position_display(self.timeline.value())

    def on_slider_moved(self, value: int) -> None:
        self.update_position_display(value)
        if self.player is not None:
            self.player.seek(value / 1000.0, "absolute+exact")

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if isinstance(QApplication.focusWidget(), (QAbstractSpinBox, QLineEdit)):
                    self.release_input_focus()
                    return True
            if event.modifiers() == Qt.KeyboardModifier.NoModifier:
                if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                    return False
        return super().eventFilter(watched, event)

    # --------------------------------------------------------------- open file

    def open_video(self) -> None:
        if self.player is None:
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Open Video", "", "Video Files (*.mp4 *.mov *.avi *.mkv *.webm *.wmv *.m4v);;All Files (*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        self.clip_play_timer.stop()
        self.input_path = path
        self.duration_seconds = 0.0
        self.source_fps = 30.0
        self.source_color_info = {}
        self._duration_set = False
        self.output_name.setText(path.stem)
        self._set_export_status(f"Loading: {path.name} ...")

        self.current_position_ms = 0
        self.start_position_ms = 0
        self._mpv_time_pos = None
        self._mpv_duration = None
        self._mpv_vid_w = None
        self._mpv_vid_h = None
        self.timeline.setRange(0, 100)
        self.timeline.setValue(0)
        self.timeline.setEnabled(False)
        self.source_resolution_lbl.setText("—")
        self.source_fps_lbl.setText("—")
        self.update_start_display()
        self.update_position_display(0)

        self.player.pause = True
        self.player.play(str(path))

        self._poll_timer.start()
        self._start_probe(path)

    def _cleanup_probe_worker(self) -> None:
        if self.probe_worker is None:
            return
        worker = self.probe_worker
        self.probe_worker = None
        try:
            if worker.isRunning():
                worker.stop()
                worker.wait(1500)
            worker.metadata_ready.disconnect()
        except RuntimeError:
            return
        try:
            worker.metadata_error.disconnect()
        except RuntimeError:
            pass
        worker.deleteLater()

    def _start_probe(self, path: Path) -> None:
        self._cleanup_probe_worker()
        self.probe_worker = ProbeWorker(path)
        self.probe_worker.metadata_ready.connect(self.on_probe_ready)
        self.probe_worker.metadata_error.connect(self.on_probe_error)
        self.probe_worker.finished.connect(self._on_probe_finished)
        self.probe_worker.start()

    def _on_probe_finished(self) -> None:
        if self.probe_worker is not None:
            self.probe_worker.deleteLater()
            self.probe_worker = None

    def on_probe_ready(
        self,
        path_str: str,
        duration: float,
        fps: float,
        width: int,
        height: int,
        color_info: dict[str, str] | None = None,
    ) -> None:
        if self.input_path is None or str(self.input_path) != path_str:
            return
        self.duration_seconds = duration
        self.source_fps = max(1.0, fps)
        self.source_width = width
        self.source_height = height
        self.source_color_info = color_info or {}
        self.video_widget.set_video_size(width, height)
        self.source_resolution_lbl.setText(f"{width}\u00d7{height}")
        self.source_fps_lbl.setText(f"{self.source_fps:.3f}")
        self._set_export_status(f"Loaded: {self.input_path.name} | FPS: {self.source_fps:.3f}")
        self.update_position_display()

    def on_probe_error(self, path_str: str, error: str) -> None:
        if self.input_path is None or str(self.input_path) != path_str:
            return
        self._set_export_status(f"Loaded without probe metadata: {self.input_path.name}")
        QMessageBox.warning(self, "Metadata warning", error)

    # ------------------------------------------------------------------ export

    def _current_export_fps(self) -> float:
        if self.override_fps.isChecked():
            return float(self.fps_spin.value())
        return float(self.source_fps)

    def _source_clip_duration_sec(self) -> float:
        target_fps = self._current_export_fps()
        effective_fps = min(self.source_fps, target_fps)
        return self.target_frames.value() / effective_fps

    def _validate_crop(self) -> bool:
        lr = self.left_crop.value() + self.right_crop.value()
        tb = self.top_crop.value() + self.bottom_crop.value()
        if lr >= 100 or tb >= 100:
            QMessageBox.warning(self, "Invalid crop", "Sum of opposite crop sides must be < 100%.")
            return False
        return True

    def _build_default_export_name(self) -> str:
        if self.input_path is None:
            return "clip.mp4"
        base = self.output_name.text().strip() or self.input_path.stem
        start_sec = self.start_position_ms / 1000.0
        end_sec = start_sec + self._source_clip_duration_sec()
        if self.duration_seconds > 0:
            end_sec = min(end_sec, self.duration_seconds)
        return (
            f"{base}_"
            f"{format_timestamp_filename(start_sec)}"
            f"_to_"
            f"{format_timestamp_filename(end_sec)}.mp4"
        )

    def _build_export_command(self, out_path: Path, add_hflip: bool) -> list[str]:
        if self.input_path is None:
            raise ValueError("Input file is not selected.")
        return ffmpeg.build_export_command(
            input_path=self.input_path,
            out_path=out_path,
            start_sec=self.start_position_ms / 1000.0,
            duration_sec=self._source_clip_duration_sec(),
            target_frames=self.target_frames.value(),
            source_fps=self.source_fps,
            left_pct=self.left_crop.value(),
            right_pct=self.right_crop.value(),
            top_pct=self.top_crop.value(),
            bottom_pct=self.bottom_crop.value(),
            override_fps=self.override_fps.isChecked(),
            output_fps=self._current_export_fps(),
            include_audio=self.include_audio.isChecked(),
            add_hflip=add_hflip,
            color_info=self.source_color_info,
        )

    def _active_export_count(self) -> int:
        return sum(1 for w in self.export_workers if w.isRunning())

    def _cleanup_export_worker(self, worker: ExportWorker, output_paths: list[Path]) -> None:
        if worker in self.export_workers:
            self.export_workers.remove(worker)
        for p in output_paths:
            self.active_export_paths.discard(str(p.resolve(strict=False)))

    def export_clip(self) -> None:
        if self.input_path is None:
            QMessageBox.warning(self, "No input", "Open an MP4 file first.")
            return
        if not self._validate_crop():
            return
        if self.target_frames.value() <= 0:
            QMessageBox.warning(self, "Invalid length", "Target frames must be greater than zero.")
            return

        default_name = self._build_default_export_name()
        output_file, _ = QFileDialog.getSaveFileName(
            self,
            "Save exported clip",
            str(self.input_path.with_name(default_name)),
            "MP4 files (*.mp4)",
        )
        if not output_file:
            return
        out_path = Path(output_file)
        output_paths = [out_path]
        if self.save_hflip.isChecked():
            output_paths.append(out_path.with_stem(f"{out_path.stem}_hflip"))
        path_keys = [str(p.resolve(strict=False)) for p in output_paths]
        for key in path_keys:
            if key in self.active_export_paths:
                QMessageBox.warning(
                    self,
                    "Export already running",
                    "An export to the same output file is already in progress.",
                )
                return
        commands: list[list[str]] = []
        try:
            commands.append(self._build_export_command(out_path, add_hflip=False))
            if self.save_hflip.isChecked():
                hflip_path = out_path.with_stem(f"{out_path.stem}_hflip")
                commands.append(self._build_export_command(hflip_path, add_hflip=True))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export setup failed", str(exc))
            return

        worker = ExportWorker(commands, job_name=out_path.name)
        for key in path_keys:
            self.active_export_paths.add(key)
        self.export_workers.append(worker)
        worker.finished_ok.connect(
            lambda message, w=worker, paths=output_paths: self.on_export_success(w, paths, message)
        )
        worker.finished_error.connect(
            lambda message, w=worker, paths=output_paths: self.on_export_error(w, paths, message)
        )
        worker.start()
        self._set_export_status(f"Exporting in background... ({self._active_export_count()} active)")

    def on_export_success(self, worker: ExportWorker, output_paths: list[Path], message: str) -> None:
        self._cleanup_export_worker(worker, output_paths)
        self._set_export_status(f"{message} ({self._active_export_count()} active)")

    def on_export_error(self, worker: ExportWorker, output_paths: list[Path], message: str) -> None:
        self._cleanup_export_worker(worker, output_paths)
        if self.is_closing and message.startswith("Export cancelled:"):
            return
        self._set_export_status("Export failed.")
        QMessageBox.critical(self, "Export failed", message)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.is_closing = True
        self._poll_timer.stop()
        self.clip_play_timer.stop()
        self._cleanup_probe_worker()

        for worker in list(self.export_workers):
            if worker.isRunning():
                worker.stop()
                worker.wait(2500)
            worker.deleteLater()
        self.export_workers.clear()
        self.active_export_paths.clear()

        self.video_widget.free_context()
        if self.player is not None:
            self.player.terminate()
            self.player = None
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    win = VideoPrepWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
