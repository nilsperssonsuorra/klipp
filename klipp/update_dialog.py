"""The window that asks before updating, then shows the download."""

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from . import __version__, updates
from .theme import stylesheet


def _label(text, role=None):
    label = QLabel(text)
    label.setWordWrap(True)
    if role:
        label.setProperty("role", role)
    return label


class UpdateDialog(QDialog):
    ready = Signal(str)  # the downloaded Klipp.exe, to hand over to

    def __init__(self, release, install, icon=None):
        super().__init__(None, Qt.WindowCloseButtonHint | Qt.WindowTitleHint)
        self.release = release
        self.downloader = updates.Downloader(release, install, self)
        self.downloader.progress.connect(self._progress)
        self.downloader.finished.connect(self._downloaded)
        self.downloader.failed.connect(self.show_error)
        self.setWindowTitle("Update Klipp")
        if icon is not None:
            self.setWindowIcon(icon)
        self.setStyleSheet(stylesheet())
        self.setFixedWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 18)
        root.setSpacing(8)
        root.addWidget(_label(f"Klipp {release.version} is available", "heading"))
        size = f" ({release.size / 1e6:.0f} MB)" if release.size else ""
        root.addWidget(_label(f"You have {__version__}. Klipp will download the update{size}, close and "
                              "start again. Your settings are kept."))
        notes = _label(f'<a href="{release.page}" style="color: #9ec0ff;">What\'s new in {release.version}</a>')
        notes.setOpenExternalLinks(True)
        root.addWidget(notes)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        self.bar.hide()
        root.addSpacing(4)
        root.addWidget(self.bar)
        self.status = _label("", "hint")
        self.status.hide()
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = QPushButton("Not now")
        self.close_button.clicked.connect(self.reject)
        self.update_button = QPushButton("Update")
        self.update_button.setProperty("role", "primary")
        self.update_button.setDefault(True)
        self.update_button.clicked.connect(self.start)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.update_button)
        root.addSpacing(6)
        root.addLayout(buttons)

    def start(self):
        self.update_button.hide()
        self.close_button.setText("Cancel")
        self.bar.setRange(0, 0)  # busy until the first bytes arrive
        self.bar.show()
        self._set_status("Downloading…")
        self.downloader.start()

    def _set_status(self, text, role="hint"):
        self.status.setText(text)
        self.status.setProperty("role", role)
        self.status.style().polish(self.status)
        self.status.show()
        self.layout().activate()
        self.adjustSize()  # grow for the progress bar and status, keeping the wrapped text whole

    def _progress(self, done, total):
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self._set_status(f"Downloading… {done / 1e6:.0f} of {total / 1e6:.0f} MB")

    def _downloaded(self, exe):
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.close_button.setEnabled(False)
        self._set_status("Restarting Klipp…")
        self.ready.emit(exe)

    def show_error(self, message):
        self.bar.hide()
        self._set_status(f"Klipp couldn't update. {message}", "error")
        self.close_button.setText("Close")
        self.close_button.setEnabled(True)
        self.update_button.setText("Open download page")
        self.update_button.clicked.disconnect()
        self.update_button.clicked.connect(self._open_page)
        self.update_button.show()

    def _open_page(self):
        QDesktopServices.openUrl(QUrl(self.release.page))
        self.accept()

    def reject(self):
        self.downloader.cancel()  # nothing happens if it never started
        super().reject()
