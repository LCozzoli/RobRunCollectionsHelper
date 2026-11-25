"""Stable-file polling, also works on drives where filesystem notifications fail."""
from pathlib import Path
import os

EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}


def steam_roots():
    roots = []
    try:
        import winreg
        keys = [(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Valve\Steam', 'InstallPath'),
                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')]
        for hive, name, value in keys:
            try:
                with winreg.OpenKey(hive, name) as key:
                    roots.append(Path(winreg.QueryValueEx(key, value)[0]))
            except OSError:
                continue
    except ImportError:
        pass
    for variable, default in [('PROGRAMFILES(X86)', r'C:\Program Files (x86)'), ('PROGRAMFILES', r'C:\Program Files')]:
        roots.append(Path(os.environ.get(variable, default))/'Steam')
    return list(dict.fromkeys(roots))


def readable_folder(folder):
    if not folder: return None
    try:
        path = Path(folder).expanduser().resolve()
        with os.scandir(path): pass
        return path
    except (OSError, ValueError):
        return None


def discover_steam_folder(roots=None):
    candidates = []
    for root in steam_roots() if roots is None else roots:
        try:
            for folder in (Path(root)/'userdata').glob('*/760/remote/4262460/screenshots'):
                if not readable_folder(folder): continue
                try: latest = FolderWatcher(folder).latest()
                except OSError: continue
                candidates.append((latest[1] if latest else 0, str(folder.resolve())))
        except OSError:
            continue
    return Path(max(candidates)[1]) if candidates else None


def startup_folder(explicit=None, saved=None, mode='auto'):
    # An explicit choice survives restarts; automatic mode is rediscovered each time.
    for candidate in (explicit, saved if mode == 'manual' else None):
        if path := readable_folder(candidate): return path, 'manual'
    if path := discover_steam_folder(): return path, 'auto'
    if path := readable_folder(saved): return path, 'manual'
    return None, 'auto'


def complete(path):
    """True once an image file ends with its format's end marker (JPEG EOI, PNG IEND).

    Steam writes the screenshot in one go, so a finished file can be analysed at the first sight of it
    instead of waiting for a second identical observation."""
    suffix = Path(path).suffix.lower()
    if suffix in ('.jpg', '.jpeg'): marker = b'\xff\xd9'
    elif suffix == '.png': marker = b'IEND\xaeB`\x82'
    else: return False
    try:
        with open(path, 'rb') as file:
            file.seek(0, os.SEEK_END)
            if file.tell() < 64: return False
            file.seek(-len(marker), os.SEEK_END)
            return file.read() == marker
    except OSError:
        return False


def fingerprint(path):
    stat = path.stat()
    return str(path), stat.st_mtime_ns, stat.st_size


class FolderWatcher:
    def __init__(self, folder, after=None):
        self.folder = Path(folder)
        self.observed = None
        self.emitted = tuple(after) if isinstance(after, (tuple, list)) and len(after) == 3 else None

    def is_older(self, current):
        return self.emitted is not None and (current[1], current[0]) < (self.emitted[1], self.emitted[0])

    def mark_processed(self, current):
        if not self.is_older(current): self.emitted = tuple(current)

    def latest(self):
        # Only direct screenshots, never Steam's thumbnails subfolder. scandir reads names and dates in
        # one directory listing (cheap even with thousands of captures); the chosen file is then
        # re-read with a real stat, because directory entries can lag behind a file being written.
        newest = None
        with os.scandir(self.folder) as entries:
            for entry in entries:
                if os.path.splitext(entry.name)[1].lower() not in EXTENSIONS: continue
                try:
                    if not entry.is_file(): continue
                    key = (entry.stat().st_mtime_ns, entry.path)
                except OSError:
                    continue
                if newest is None or key > newest: newest = key
        if newest is None: return None
        try:
            return fingerprint(Path(newest[1]))
        except OSError:
            return None

    def poll(self):
        current = self.latest()
        if current is None:
            self.observed = None
            return None
        stable = current == self.observed
        self.observed = current
        if current == self.emitted or self.is_older(current) or current[2] <= 0: return None
        # Removing a processed capture must never trigger a scan of older files.
        if stable or complete(current[0]):
            self.emitted = current
            return Path(current[0])
        return None

    def retry(self):
        self.emitted = None
