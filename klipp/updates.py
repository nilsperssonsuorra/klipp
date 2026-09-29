"""Checking GitHub for a newer release.

Only a plain HTTPS request to GitHub's public releases API is made; nothing about the user
or their captures is sent. A failed check (offline, private repo, rate limit) is silent.
"""

import json
import re
import threading
import urllib.request

from PySide6.QtCore import QObject, QTimer, Signal

from . import __version__

REPO = "nilsperssonsuorra/klipp"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
FIRST_CHECK_MS = 15_000  # shortly after start, not during it
CHECK_EVERY_MS = 24 * 60 * 60 * 1000


def parse_version(text):
    """"v1.10.2" -> (1, 10, 2), for comparing."""
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def fetch_latest(timeout=10):
    """(version, release page URL) of the latest published release."""
    request = urllib.request.Request(RELEASES_API, headers={
        "User-Agent": f"Klipp/{__version__}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        release = json.load(response)
    if release.get("draft") or release.get("prerelease"):
        return None
    return release["tag_name"].lstrip("v"), release["html_url"]


class UpdateChecker(QObject):
    """Checks shortly after start and then once a day, in a background thread."""

    found = Signal(str, str)  # newer version, release page URL

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self, timeout=self.check)

    def start(self):
        self._timer.start(CHECK_EVERY_MS)
        QTimer.singleShot(FIRST_CHECK_MS, self.check)

    def stop(self):
        self._timer.stop()

    def check(self):
        if self._timer.isActive():
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            latest = fetch_latest()
        except Exception:  # offline, private repo, rate limited, bad response: try again tomorrow
            return
        if latest and parse_version(latest[0]) > parse_version(__version__):
            self.found.emit(*latest)  # delivered on the main thread
