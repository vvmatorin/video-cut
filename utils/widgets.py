from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QOpenGLContext, QPainter, QPen
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    import mpv


class CropOverlayWidget(QWidget):
    """Transparent widget drawn on top of the mpv video surface to show crop guides."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.left_pct = 0.0
        self.right_pct = 0.0
        self.top_pct = 0.0
        self.bottom_pct = 0.0
        self.video_width = 0
        self.video_height = 0

    def set_crop(self, left: float, right: float, top: float, bottom: float) -> None:
        self.left_pct = left
        self.right_pct = right
        self.top_pct = top
        self.bottom_pct = bottom
        self.update()

    def set_video_size(self, width: int, height: int) -> None:
        self.video_width = width
        self.video_height = height
        self.update()

    def _video_rect(self) -> QRectF:
        if self.video_width <= 0 or self.video_height <= 0:
            return QRectF(0, 0, self.width(), self.height())
        image_w = float(self.video_width)
        image_h = float(self.video_height)
        widget_w = float(self.width())
        widget_h = float(self.height())
        scale = min(widget_w / image_w, widget_h / image_h)
        draw_w = image_w * scale
        draw_h = image_h * scale
        x = (widget_w - draw_w) / 2.0
        y = (widget_h - draw_h) / 2.0
        return QRectF(x, y, draw_w, draw_h)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.video_width <= 0 or self.video_height <= 0:
            return
        if all(v == 0.0 for v in (self.left_pct, self.right_pct, self.top_pct, self.bottom_pct)):
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        vr = self._video_rect()
        w = vr.width()
        h = vr.height()
        x0 = vr.x()
        y0 = vr.y()
        left = w * self.left_pct / 100.0
        right = w * self.right_pct / 100.0
        top = h * self.top_pct / 100.0
        bottom = h * self.bottom_pct / 100.0

        shade = QColor(255, 0, 0, 60)
        painter.fillRect(QRectF(x0, y0, left, h), shade)
        painter.fillRect(QRectF(max(x0, x0 + w - right), y0, right, h), shade)
        painter.fillRect(QRectF(x0 + left, y0, max(0.0, w - left - right), top), shade)
        painter.fillRect(
            QRectF(x0 + left, max(y0, y0 + h - bottom), max(0.0, w - left - right), bottom),
            shade,
        )

        pen = QPen(QColor(0, 255, 0, 200))
        pen.setWidth(2)
        painter.setPen(pen)
        crop_w = max(1.0, w - left - right)
        crop_h = max(1.0, h - top - bottom)
        painter.drawRect(QRectF(x0 + left, y0 + top, crop_w, crop_h))
        painter.end()


class MpvOpenGLWidget(QOpenGLWidget):
    """Renders mpv video frames via the libmpv OpenGL render API."""

    _frame_ready = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx: mpv.MpvRenderContext | None = None
        self._frame_ready.connect(self._schedule_repaint, Qt.ConnectionType.QueuedConnection)

    def init_render_context(self, player: mpv.MPV) -> None:
        import mpv as _mpv

        self.makeCurrent()

        @_mpv.MpvGlGetProcAddressFn
        def get_proc_address(_ctx, name: bytes) -> int:  # type: ignore[arg-type]
            glctx = QOpenGLContext.currentContext()
            if glctx is None:
                return 0
            return glctx.getProcAddress(name) or 0

        self._get_proc_address_fn = get_proc_address  # prevent GC

        self._ctx = _mpv.MpvRenderContext(
            player,
            "opengl",
            opengl_init_params={"get_proc_address": get_proc_address},
        )
        self._ctx.update_cb = self._on_frame_available
        self.doneCurrent()

    def _on_frame_available(self) -> None:
        self._frame_ready.emit()

    @Slot()
    def _schedule_repaint(self) -> None:
        self.update()

    def paintGL(self) -> None:
        if self._ctx is None:
            return
        ratio = self.devicePixelRatio()
        w = int(self.width() * ratio)
        h = int(self.height() * ratio)
        self._ctx.render(
            flip_y=True,
            opengl_fbo={"fbo": self.defaultFramebufferObject(), "w": w, "h": h},
        )

    def free_context(self) -> None:
        if self._ctx is not None:
            self.makeCurrent()
            self._ctx.free()
            self._ctx = None
            self.doneCurrent()


class MpvWidget(QWidget):
    """Container: mpv OpenGL rendering + crop overlay on top."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gl_widget = MpvOpenGLWidget(self)
        self._overlay = CropOverlayWidget(self)
        self._overlay.raise_()

    def init_render_context(self, player: mpv.MPV) -> None:
        self._gl_widget.init_render_context(player)

    def free_context(self) -> None:
        self._gl_widget.free_context()

    def set_crop(self, left: float, right: float, top: float, bottom: float) -> None:
        self._overlay.set_crop(left, right, top, bottom)

    def set_video_size(self, width: int, height: int) -> None:
        self._overlay.set_video_size(width, height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._gl_widget.setGeometry(self.rect())
        self._overlay.setGeometry(self.rect())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)
