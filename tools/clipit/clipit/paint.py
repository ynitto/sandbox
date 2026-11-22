from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)


def png_bytes(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenMode.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("画像を PNG に変換できませんでした。")
    return bytes(buffer.data())


class Canvas(QWidget):
    def __init__(self):
        super().__init__()
        self.color = QColor("#202020")
        self.width_px = 5
        self.erasing = False
        self.previous = None
        self.load(QImage())

    def load(self, image):
        self.previous = None
        if image.isNull():
            image = QImage(720, 280, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.white)
        self.background = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.layer = QImage(self.background.size(), self.background.format())
        self.clear()
        self.setFixedSize(self.background.size())

    def clear(self):
        self.layer.fill(Qt.GlobalColor.transparent)
        self.update()

    def composite(self):
        image = self.background.copy()
        with QPainter(image) as painter:
            painter.drawImage(0, 0, self.layer)
        return image

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.drawImage(0, 0, self.background)
            painter.drawImage(0, 0, self.layer)

    def _stroke(self, point):
        with QPainter(self.layer) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if self.erasing:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.setPen(QPen(
                self.color, self.width_px, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap
            ))
            if self.previous == point:
                painter.drawPoint(point)
            else:
                painter.drawLine(self.previous, point)
        self.previous = point
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.previous = event.position().toPoint()
            self._stroke(self.previous)

    def mouseMoveEvent(self, event):
        if self.previous is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._stroke(event.position().toPoint())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.previous is not None:
            self._stroke(event.position().toPoint())
            self.previous = None


class PaintTab(QWidget):
    def __init__(self, save_image):
        super().__init__()
        self.canvas = Canvas()
        self.save_image = save_image
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        layout.addLayout(tools)
        self.color_button = QPushButton("色")
        self.color_button.clicked.connect(self.choose_color)
        tools.addWidget(self.color_button)
        tools.addWidget(QLabel("太さ"))
        width = QSpinBox()
        width.setRange(1, 60)
        width.setValue(self.canvas.width_px)
        width.valueChanged.connect(self.set_width)
        tools.addWidget(width)
        eraser = QCheckBox("消去")
        eraser.toggled.connect(self.set_eraser)
        tools.addWidget(eraser)
        for label, action in (
            ("全消し", self.canvas.clear),
            ("空白キャンバス", lambda: self.canvas.load(QImage())),
            ("画像クリップに追加", self.save),
        ):
            button = QPushButton(label)
            button.clicked.connect(action)
            tools.addWidget(button)
        tools.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll)

    def set_width(self, width):
        self.canvas.width_px = width

    def set_eraser(self, erasing):
        self.canvas.erasing = erasing

    def choose_color(self):
        color = QColorDialog.getColor(self.canvas.color, self)
        if color.isValid():
            self.canvas.color = color
            swatch = QPixmap(16, 16)
            swatch.fill(color)
            self.color_button.setIcon(QIcon(swatch))

    def save(self):
        self.save_image(self.canvas.composite())
