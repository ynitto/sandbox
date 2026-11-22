from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)


class NotesTab(QWidget):
    def __init__(self, store, copy_text, report_error):
        super().__init__()
        self.store = store
        self.copy_text = copy_text
        self.report_error = report_error
        layout = QVBoxLayout(self)
        entry = QHBoxLayout()
        layout.addLayout(entry)
        self.input = QLineEdit()
        self.input.setPlaceholderText("タスクを入力")
        self.input.returnPressed.connect(self.add_note)
        entry.addWidget(self.input)
        add = QPushButton("追加")
        add.clicked.connect(self.add_note)
        entry.addWidget(add)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.rows = QVBoxLayout(container)
        self.rows.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)
        for note in store.notes:
            self.make_row(note)

    def make_row(self, note):
        row = QWidget()
        layout = QHBoxLayout(row)
        check = QCheckBox(note["text"])
        check.setChecked(note["checked"])
        check.toggled.connect(lambda checked: self.toggle(note, check, checked))
        layout.addWidget(check, 1)
        copy = QPushButton("コピー")
        copy.clicked.connect(lambda: self.copy_text(note["text"]))
        layout.addWidget(copy)
        delete = QPushButton("削除")
        delete.clicked.connect(lambda: self.delete(note, row))
        layout.addWidget(delete)
        self.rows.insertWidget(self.rows.count() - 1, row)

    def persist(self, notes):
        try:
            self.store.save_notes(notes)
        except (OSError, ValueError) as error:
            self.report_error("メモを保存できませんでした", error)
            return False
        return True

    def add_note(self):
        text = self.input.text().strip()
        if text:
            note = {"text": text, "checked": False}
            if self.persist([*self.store.notes, note]):
                self.make_row(note)
                self.input.clear()

    def toggle(self, note, check, checked):
        note["checked"] = checked
        if not self.persist(self.store.notes):
            note["checked"] = not checked
            check.blockSignals(True)
            check.setChecked(not checked)
            check.blockSignals(False)

    def delete(self, note, row):
        if self.persist([item for item in self.store.notes if item is not note]):
            self.rows.removeWidget(row)
            row.deleteLater()
