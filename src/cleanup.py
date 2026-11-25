"""Recycle only the exact, successfully analyzed screenshot in the watched folder."""
from pathlib import Path
from watcher import EXTENSIONS, fingerprint
from i18n import _


def recycle_capture(path, expected, folder):
    path = Path(path)
    if (path.is_symlink() or path.suffix.lower() not in EXTENSIONS
            or path.parent.resolve() != Path(folder).resolve()):
        raise OSError(_('Cette image ne fait pas partie du dossier surveillé.'))
    if fingerprint(path) != tuple(expected):
        raise OSError(_('La capture a changé depuis son analyse ; elle est conservée.'))
    # Use the modern Windows backend explicitly: packaging failures must not
    # silently fall back to a different deletion mechanism.
    from send2trash.win.modern import send2trash
    send2trash(str(path.resolve()))
    if path.exists():
        raise OSError(_('La capture n’a pas pu être déplacée dans la Corbeille.'))
