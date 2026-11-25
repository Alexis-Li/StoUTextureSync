"""Painter dock widget for the M1a sync controls."""

from PySide6 import QtCore, QtWidgets


class SyncPanel(QtWidgets.QWidget):
    realtime_toggled = QtCore.Signal(bool)
    immediate_requested = QtCore.Signal()
    full_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("stou_texture_sync_panel")
        self.setWindowTitle("StoUTextureSync")

        self.realtime_toggle = QtWidgets.QCheckBox("实时同步")
        self.realtime_toggle.setObjectName("realtime_toggle")
        self.immediate_sync_button = QtWidgets.QPushButton("立即同步")
        self.immediate_sync_button.setObjectName("immediate_sync_button")
        self.full_sync_button = QtWidgets.QPushButton("完整同步")
        self.full_sync_button.setObjectName("full_sync_button")
        self.status_label = QtWidgets.QLabel()
        self.status_label.setObjectName("status_label")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.realtime_toggle)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.immediate_sync_button)
        buttons.addWidget(self.full_sync_button)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addStretch()

        self.realtime_toggle.toggled.connect(self.realtime_toggled)
        self.immediate_sync_button.clicked.connect(self.immediate_requested)
        self.full_sync_button.clicked.connect(self.full_requested)

    def set_realtime_checked(self, checked: bool) -> None:
        with QtCore.QSignalBlocker(self.realtime_toggle):
            self.realtime_toggle.setChecked(checked)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def status_text(self) -> str:
        return self.status_label.text()

    def is_realtime_checked(self) -> bool:
        return self.realtime_toggle.isChecked()

    def set_configured(self, configured: bool) -> None:
        self.realtime_toggle.setEnabled(configured)
        self.immediate_sync_button.setEnabled(configured)
        self.full_sync_button.setEnabled(configured)
