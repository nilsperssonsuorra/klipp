"""Checking GitHub for a newer release, and installing it when the user asks.

Only plain HTTPS requests to GitHub are made; nothing about the user or their captures is
sent. A failed check (offline, private repo, rate limit) is silent. Nothing is downloaded
until the user presses Update.

Installing happens in two steps, so the new version has shown that it starts before the
old one is touched:
1. The running Klipp downloads the release zip next to its folder, checks its size and
   checksum, unpacks it and starts the new Klipp.exe with --apply-update. Then it quits.
2. The new Klipp.exe waits for the old one to close, moves the old folder aside, copies
   itself into its place and starts from there with --updated, which deletes the leftovers.
   If anything goes wrong, the old folder is put back and started with --update-failed.
"""

import ctypes
import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections import namedtuple
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import __version__

REPO = "nilsperssonsuorra/klipp"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"  # GitHub then redirects to its file storage
FIRST_CHECK_MS = 15_000  # shortly after start, not during it
CHECK_EVERY_MS = 24 * 60 * 60 * 1000
EXE_NAME = "Klipp.exe"
# The new version does the swap (step 2 below), so only versions that know how can be installed.
FIRST_SELF_INSTALLING = (1, 5, 0)
# Flags the old and new versions pass each other. Later versions must keep accepting them.
APPLY_FLAG = "--apply-update"  # <old process id> <old folder>
UPDATED_FLAG = "--updated"
FAILED_FLAG = "--update-failed"  # <reason>

Release = namedtuple("Release", "version page download_url size sha256")


class UpdateError(Exception):
    """Something the user should be told, in words they can act on."""


class Cancelled(Exception):
    pass


def parse_version(text):
    """"v1.10.2" -> (1, 10, 2), for comparing."""
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def fetch_latest(timeout=10):
    """The latest published release, or None."""
    request = urllib.request.Request(RELEASES_API, headers={
        "User-Agent": f"Klipp/{__version__}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return parse_release(json.load(response))


def parse_release(data):
    """A Release from GitHub's API response. download_url is None if it can't be installed
    from inside Klipp: there's no Windows zip, or it's too old to swap itself in."""
    if data.get("draft") or data.get("prerelease"):
        return None
    version = data["tag_name"].lstrip("v")
    name = f"Klipp-{version}-windows.zip"
    asset = next((a for a in data.get("assets", []) if a.get("name") == name), None)
    if asset is None or parse_version(version) < FIRST_SELF_INSTALLING:
        return Release(version, data["html_url"], None, 0, None)
    digest = asset.get("digest") or ""
    sha256 = digest.removeprefix("sha256:").lower() if digest.startswith("sha256:") else None
    return Release(version, data["html_url"], asset["browser_download_url"], asset["size"], sha256)


class UpdateChecker(QObject):
    """Checks shortly after start and then once a day, in a background thread."""

    found = Signal(object)  # a newer Release

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
        if latest and parse_version(latest.version) > parse_version(__version__):
            self.found.emit(latest)  # delivered on the main thread


# --- installing -------------------------------------------------------------------


def install_dir():
    """The folder Klipp.exe runs from, or None when running from source (nothing to replace)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return None


def staging_dir(install):
    return install.with_name(install.name + ".update")


def backup_dir(install):
    return install.with_name(install.name + ".old")


def describe(error):
    if isinstance(error, UpdateError):
        return str(error)
    if isinstance(error, urllib.error.URLError):
        return f"Couldn't download the update ({error.reason})."
    return str(error) or type(error).__name__


def download_release(release, staging, cancel=None, progress=None, timeout=30):
    """Download `release` into `staging`, check it and unpack it. Returns the new Klipp.exe."""
    if not release.download_url.startswith(DOWNLOAD_PREFIX):
        raise UpdateError(f"Klipp only installs updates from GitHub, not {release.download_url}")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    archive = staging / "download.zip"
    digest = hashlib.sha256()
    done = 0
    request = urllib.request.Request(release.download_url, headers={"User-Agent": f"Klipp/{__version__}"})
    with urllib.request.urlopen(request, timeout=timeout) as response, archive.open("wb") as out:
        while chunk := response.read(256 * 1024):
            if cancel is not None and cancel.is_set():
                raise Cancelled
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, release.size)
    if release.size and done != release.size:
        raise UpdateError("The download was incomplete. Please try again.")
    if release.sha256 and digest.hexdigest() != release.sha256:
        raise UpdateError("The download doesn't match the release's checksum, so it wasn't installed.")
    with zipfile.ZipFile(archive) as z:
        z.extractall(staging / "files")
    archive.unlink()
    exe = next((staging / "files").glob(f"*/{EXE_NAME}"), None)
    if exe is None:
        raise UpdateError(f"The download has no {EXE_NAME} in it.")
    return exe


class Downloader(QObject):
    """Runs download_release in a background thread and reports back on the main thread."""

    progress = Signal(int, int)  # bytes so far, total
    finished = Signal(str)  # the unpacked Klipp.exe
    failed = Signal(str)  # what went wrong, for the user

    def __init__(self, release, install, parent=None):
        super().__init__(parent)
        self.release = release
        self.staging = staging_dir(install)
        self._cancel = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self._cancel.set()

    def _run(self):
        try:
            exe = download_release(self.release, self.staging, self._cancel, self.progress.emit)
        except Exception as error:
            shutil.rmtree(self.staging, ignore_errors=True)
            if not isinstance(error, Cancelled):
                self.failed.emit(describe(error))
            return
        self.finished.emit(str(exe))


def launch(exe, *args):
    """Start `exe` on its own, so it keeps running after this process quits. Its working
    folder is its own, so it never holds on to a folder that's about to be moved."""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(exe), *args], cwd=str(Path(exe).parent), creationflags=flags, close_fds=True)


def wait_for_exit(pid, timeout):
    SYNCHRONIZE, WAIT_OBJECT_0 = 0x00100000, 0
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return True  # already gone
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def retry(action, attempts=20, delay=0.25):
    """Antivirus or Explorer can hold on to a file for a moment just after it's written or closed."""
    for attempt in range(attempts):
        try:
            return action()
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def apply_update(new_dir, target, old_pid, start=launch, wait=30):
    """Step 2, run by the new Klipp.exe from the staging folder: once the old Klipp has
    closed, put `new_dir` where `target` is and start it. Returns True if it worked."""
    backup = backup_dir(target)
    try:
        if not wait_for_exit(old_pid, wait):
            raise UpdateError("The running Klipp didn't close.")
        shutil.rmtree(backup, ignore_errors=True)
        retry(lambda: target.rename(backup))
    except Exception as error:
        start(target / EXE_NAME, FAILED_FLAG, describe(error))
        return False
    try:
        shutil.copytree(new_dir, target)
    except Exception as error:
        shutil.rmtree(target, ignore_errors=True)
        retry(lambda: backup.rename(target))
        start(target / EXE_NAME, FAILED_FLAG, describe(error))
        return False
    start(target / EXE_NAME, UPDATED_FLAG)
    return True


def run_apply(args):
    """`Klipp.exe --apply-update <old process id> <old folder>`."""
    apply_update(Path(sys.executable).parent, Path(args[1]), int(args[0]))


def clean_up(install):
    """Delete what an update left next to `install`. Returns True once both are gone."""
    leftovers = (staging_dir(install), backup_dir(install))
    for path in leftovers:
        shutil.rmtree(path, ignore_errors=True)
    return not any(path.exists() for path in leftovers)
