import sys
from queue import Empty, SimpleQueue
from threading import Thread

from PySide6.QtCore import QMimeData, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QStyle,
    QSystemTrayIcon, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from .calc import evaluate
from .hotkey import Hotkey
from .notes import NotesTab
from .ocr import OcrUnavailable, recognize
from .paint import PaintTab, png_bytes
from .paste import activate_window, foreground_window, modifiers_pressed, paste_target, send_paste
from .store import Store
from .translate import translate

CLIP_ID = Qt.ItemDataRole.UserRole
OWN_CLIPBOARD = "application/x-clipit-internal"


def button(label, callback, layout):
    widget = QPushButton(label)
    widget.clicked.connect(callback)
    layout.addWidget(widget)
    return widget


class HistoryList(QListWidget):
    paste_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentItem():
            self.paste_requested.emit()
            return
        super().keyPressEvent(event)


class Panel(QWidget):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.clipboard = QApplication.clipboard()
        self.target = None
        self.results = SimpleQueue()
        self.running = set()
        self.setWindowTitle("ClipIt")
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )

        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("ClipIt — Ctrl+Shift+V")
        menu = QMenu(self)
        menu.addAction("表示", self.show_panel)
        menu.addAction("終了", QApplication.instance().quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("ClipIt"))
        header.addStretch()
        button("隠す", self.hide, header)
        button("終了", QApplication.instance().quit, header)
        root.addLayout(header)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self.history = self.make_history()
        self.tabs.addTab(self.history, "履歴")
        self.paint_tab = PaintTab(self.add_image)
        self.tabs.addTab(self.paint_tab, "ペイント")
        self.tabs.addTab(NotesTab(store, self.copy_text, self.report_error), "メモ")
        self.tabs.addTab(self.make_calculator(), "電卓")

        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.read_results)
        self.timer.timeout.connect(self.remember_target)
        self.timer.start(100)
        self.refresh()
        self.clipboard.dataChanged.connect(self.capture_clipboard)

    def make_history(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("キーワード検索")
        self.search.textChanged.connect(self.refresh)
        filters.addWidget(self.search, 1)
        self.tags = QComboBox()
        self.tags.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.tags)
        self.order = QComboBox()
        self.order.addItems(["新しい順", "古い順"])
        self.order.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.order)
        layout.addLayout(filters)

        content = QHBoxLayout()
        self.clips = HistoryList()
        self.clips.setIconSize(QSize(84, 60))
        self.clips.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.clips.currentItemChanged.connect(self.update_preview)
        self.clips.itemDoubleClicked.connect(self.paste_item)
        self.clips.paste_requested.connect(self.paste_item)
        content.addWidget(self.clips, 2)
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        content.addWidget(self.preview, 3)
        layout.addLayout(content)

        actions = QHBoxLayout()
        self.edit_button = button("タグ編集", self.edit_tags, actions)
        self.pin_button = button("ピン留め", self.toggle_pin, actions)
        self.delete_button = button("削除", self.delete_clip, actions)
        self.translate_button = button("翻訳", self.translate_clip, actions)
        self.ocr_button = button("文字読み取り", self.ocr_clip, actions)
        self.paint_button = button("ペイントで開く", self.open_paint, actions)
        actions.addStretch()
        layout.addLayout(actions)
        return tab

    def make_calculator(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.expression = QLineEdit()
        self.expression.setPlaceholderText("例  (12 + 8) × 50%")
        self.expression.returnPressed.connect(self.calculate)
        row.addWidget(self.expression)
        button("計算", self.calculate, row)
        button("式と答えをクリップに追加", self.save_calculation, row)
        layout.addLayout(row)
        self.answer = QLabel()
        self.answer.setTextFormat(Qt.TextFormat.PlainText)
        self.answer.setWordWrap(True)
        self.answer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.expression.textChanged.connect(self.answer.clear)
        layout.addWidget(self.answer)
        layout.addStretch()
        return tab

    def calculate(self):
        expression = self.expression.text().strip()
        try:
            result = evaluate(expression)
        except ValueError as error:
            self.answer.setText(str(error))
            return None
        formatted = str(result) if isinstance(result, int) else format(result, ".15g")
        equation = f"{expression} = {formatted}"
        self.answer.setText(equation)
        return equation

    def save_calculation(self):
        equation = self.calculate()
        if equation is not None:
            self.add_text(equation)

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle()

    def show_panel(self):
        if not self.isVisible():
            self.remember_target()
            screen = QApplication.screenAt(self.cursor().pos()) or QApplication.primaryScreen()
            area = screen.availableGeometry()
            self.resize(area.width(), max(self.minimumSizeHint().height(), min(440, area.height() // 2)))
            self.move(area.left(), area.bottom() - self.height() + 1)
        self.show()
        self.raise_()
        self.activateWindow()
        if self.tabs.currentWidget() is self.history:
            self.clips.setFocus()

    def remember_target(self):
        if not self.isVisible():
            window = paste_target()
            if window and window != int(self.winId()):
                self.target = window

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_panel()

    def closeEvent(self, event):
        if self.tray.isVisible():
            event.ignore()
            self.hide()
        else:
            event.accept()
            QApplication.instance().quit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def report_error(self, title, error):
        message = f"{title}（{error}）"
        self.status.setText(message)
        if self.tray.isVisible():
            self.tray.showMessage("ClipIt", message, QSystemTrayIcon.MessageIcon.Warning)

    def capture_clipboard(self):
        try:
            mime = self.clipboard.mimeData()
            if mime is None or mime.hasFormat(OWN_CLIPBOARD):
                return
            if mime.hasImage():
                image = self.clipboard.image()
                if not image.isNull():
                    self.store.add_image(png_bytes(image))
                    self.refresh()
                    return
            if mime.hasText():
                value = mime.text()
                if value:
                    self.store.add_text(value)
                    self.refresh()
        except Exception as error:
            self.report_error("クリップボード履歴を保存できませんでした", error)

    def add_text(self, text, reveal=False):
        try:
            clip = self.store.add_text(text)
            if reveal:
                self.search.clear()
                self.tags.setCurrentIndex(0)
                self.tabs.setCurrentWidget(self.history)
            self.refresh(clip["id"])
            return True
        except (OSError, ValueError) as error:
            self.report_error("テキストを保存できませんでした", error)
            return False

    def add_image(self, image):
        try:
            clip = self.store.add_image(png_bytes(image))
            self.refresh(clip["id"])
            self.status.setText("画像を履歴に追加しました。")
        except (OSError, ValueError) as error:
            self.report_error("画像を保存できませんでした", error)

    def set_clipboard(self, *, text=None, image=None):
        mime = QMimeData()
        mime.setData(OWN_CLIPBOARD, b"1")
        if image is not None:
            mime.setImageData(image)
        else:
            mime.setText(text)
        self.clipboard.setMimeData(mime)

    def copy_text(self, text):
        try:
            self.set_clipboard(text=text)
            if self.add_text(text):
                self.status.setText("クリップボードにコピーしました。")
        except Exception as error:
            self.report_error("コピーできませんでした", error)

    def selected(self, item=None):
        item = self.clips.currentItem() if item is None else item
        if item is None:
            return None
        clip_id = item.data(CLIP_ID)
        return next((clip for clip in self.store.clips if clip["id"] == clip_id), None)

    def refresh(self, select_id=None):
        # Filter signals carry the new text or index rather than a clip ID.
        current = self.selected()
        if not isinstance(select_id, str) or not any(c["id"] == select_id for c in self.store.clips):
            select_id = current["id"] if current else None
        tag = self.tags.currentData()
        self.tags.blockSignals(True)
        self.tags.clear()
        self.tags.addItem("すべてのタグ", None)
        tags = {tag for clip in self.store.clips for tag in clip["tags"]}
        for label in sorted(tags, key=str.casefold):
            self.tags.addItem(label, label)
        self.tags.setCurrentIndex(max(0, self.tags.findData(tag)) if tag is not None else 0)
        self.tags.blockSignals(False)
        tag = self.tags.currentData()
        query = self.search.text().strip().casefold()
        ordered = self.store.clips if self.order.currentIndex() == 0 else reversed(self.store.clips)
        self.clips.blockSignals(True)
        self.clips.clear()
        selected_item = None
        for clip in sorted(ordered, key=lambda clip: not clip["pinned"]):
            if tag is not None and tag not in clip["tags"]:
                continue
            haystack = " ".join([clip.get("text", ""), *clip["tags"]]).casefold()
            if query not in haystack:
                continue
            label = " ".join(clip.get("text", "画像").split())[:100]
            if clip["pinned"]:
                label = f"📌 {label}"
            if clip["tags"]:
                label += "\n" + ", ".join(clip["tags"])
            item = QListWidgetItem(label)
            item.setData(CLIP_ID, clip["id"])
            if clip["kind"] == "image":
                pixmap = QPixmap(str(self.store.image_path(clip)))
                if not pixmap.isNull():
                    item.setIcon(QIcon(pixmap.scaled(
                        self.clips.iconSize(), Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )))
            self.clips.addItem(item)
            if clip["id"] == select_id:
                selected_item = item
        if selected_item is not None:
            self.clips.setCurrentItem(selected_item)
            self.clips.scrollToItem(selected_item)
        elif self.clips.count():
            self.clips.setCurrentRow(0)
        self.clips.blockSignals(False)
        self.update_preview()

    def update_preview(self, *_):
        self.preview.clear()
        clip = self.selected()
        is_text = clip is not None and clip["kind"] == "text"
        is_image = clip is not None and clip["kind"] == "image"
        if is_text:
            self.preview.setPlainText(clip["text"])
        elif is_image:
            image = QImage(str(self.store.image_path(clip)))
            if image.isNull():
                self.preview.setPlainText("画像ファイルを読み込めません。")
            else:
                image = image.scaled(
                    QSize(max(1, self.preview.viewport().width() - 12), 275),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.preview.textCursor().insertImage(image)
        for widget in (self.edit_button, self.pin_button, self.delete_button):
            widget.setEnabled(clip is not None)
        self.pin_button.setText("ピン解除" if clip and clip["pinned"] else "ピン留め")
        self.translate_button.setEnabled(is_text and "翻訳" not in self.running)
        self.ocr_button.setEnabled(is_image and "文字読み取り" not in self.running)
        self.paint_button.setEnabled(is_image)

    def edit_tags(self):
        clip = self.selected()
        if clip is None:
            return
        value, accepted = QInputDialog.getText(
            self, "タグ編集", "カンマ区切りで入力", text=", ".join(clip["tags"])
        )
        if accepted:
            tags = list(dict.fromkeys(tag.strip() for tag in value.split(",") if tag.strip()))
            self.update_clip(clip, tags=tags)

    def toggle_pin(self):
        clip = self.selected()
        if clip:
            self.update_clip(clip, pinned=not clip["pinned"])

    def update_clip(self, clip, **changes):
        try:
            self.store.update_clip(clip["id"], **changes)
            self.refresh(clip["id"])
        except (OSError, ValueError) as error:
            self.report_error("履歴を更新できませんでした", error)

    def delete_clip(self):
        clip = self.selected()
        if clip:
            try:
                self.store.delete_clip(clip["id"])
                self.refresh()
            except (OSError, ValueError) as error:
                self.report_error("履歴を削除できませんでした", error)

    def paste_item(self, item=None):
        clip = self.selected(item)
        if clip is None or not self.isVisible():
            return
        try:
            if clip["kind"] == "text":
                self.set_clipboard(text=clip["text"])
            else:
                image = QImage(str(self.store.image_path(clip)))
                if image.isNull():
                    raise ValueError("画像ファイルを読み込めません。")
                self.set_clipboard(image=image)
            self.hide()
            target = self.target
            activate_window(target)
            QTimer.singleShot(60, lambda: self.finish_paste(target, 20))
        except Exception as error:
            self.report_error("貼り付けに失敗しました", error)

    def finish_paste(self, target, retries):
        try:
            if retries and (modifiers_pressed() or (target and foreground_window() != target)):
                QTimer.singleShot(50, lambda: self.finish_paste(target, retries - 1))
                return
            if modifiers_pressed():
                raise RuntimeError("修飾キーを離して Ctrl+V で貼り付けてください。")
            if self.isVisible():
                return
            send_paste(target)
        except Exception as error:
            self.report_error("貼り付けに失敗しました", error)

    def translate_clip(self):
        clip = self.selected()
        if clip and clip["kind"] == "text":
            self.start_work("翻訳", translate, clip["text"])

    def ocr_clip(self):
        clip = self.selected()
        if clip and clip["kind"] == "image":
            self.start_work("文字読み取り", recognize, self.store.image_path(clip))

    def open_paint(self):
        clip = self.selected()
        if clip and clip["kind"] == "image":
            image = QImage(str(self.store.image_path(clip)))
            if image.isNull():
                self.report_error("画像を開けませんでした", "PNG ファイルを読み込めません。")
                return
            self.paint_tab.canvas.load(image)
            self.tabs.setCurrentWidget(self.paint_tab)

    def start_work(self, label, operation, argument):
        if label in self.running:
            return
        self.running.add(label)
        self.status.setText(f"{label}を実行しています…")
        self.update_preview()
        results = self.results

        def work():
            try:
                results.put((label, operation(argument), None))
            except Exception as error:
                results.put((label, None, error))

        try:
            Thread(target=work, daemon=True).start()
        except RuntimeError as error:
            results.put((label, None, error))

    def read_results(self):
        while True:
            try:
                label, value, error = self.results.get_nowait()
            except Empty:
                return
            self.running.discard(label)
            self.update_preview()
            if isinstance(error, OcrUnavailable):
                self.show_panel()
                QMessageBox.information(self, "OCR の有効化", str(error))
            elif error is not None:
                self.report_error(f"{label}に失敗しました", error)
            elif self.add_text(value, reveal=True):
                self.status.setText(f"{label}の結果を履歴に追加しました。")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ClipIt")
    app.setQuitOnLastWindowClosed(False)
    try:
        panel = Panel(Store())
    except (OSError, ValueError) as error:
        QMessageBox.critical(None, "ClipIt", f"保存データを開けませんでした。\n{error}")
        return 1
    hotkey = Hotkey(panel.toggle)
    app.installNativeEventFilter(hotkey)
    app.aboutToQuit.connect(hotkey.close)
    if QSystemTrayIcon.isSystemTrayAvailable():
        panel.tray.show()
    registered = False
    try:
        registered = hotkey.register()
    except Exception as error:
        panel.report_error("ホットキーを登録できませんでした", error)
    if not registered:
        panel.report_error("Ctrl+Shift+V を登録できませんでした", "トレイから ClipIt を表示できます。")
    if not registered or not panel.tray.isVisible():
        panel.show_panel()
    return app.exec()
