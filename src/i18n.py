"""Interface languages: automatic choice (game, then Steam, then Windows), French source texts as keys."""
from __future__ import annotations
import json, logging, os, re, sys
from pathlib import Path

# Native names, in menu order. The game itself ships the first six; the others translate the interface and
# use the English item names.
LANGUAGES = {
    'fr': 'Français', 'en': 'English', 'de': 'Deutsch', 'es': 'Español', 'pt': 'Português', 'ru': 'Русский',
    'it': 'Italiano', 'pl': 'Polski', 'tr': 'Türkçe', 'uk': 'Українська', 'zh': '简体中文', 'ja': '日本語', 'ko': '한국어',
}
GAME_LANGUAGES = ('fr', 'en', 'de', 'es', 'pt', 'ru')
STEAM_NAMES = {'french': 'fr', 'english': 'en', 'german': 'de', 'spanish': 'es', 'latam': 'es', 'portuguese': 'pt',
               'brazilian': 'pt', 'russian': 'ru', 'italian': 'it', 'polish': 'pl', 'turkish': 'tr', 'ukrainian': 'uk',
               'schinese': 'zh', 'tchinese': 'zh', 'japanese': 'ja', 'koreana': 'ko', 'korean': 'ko'}
WINDOWS_PRIMARY = {0x0c: 'fr', 0x09: 'en', 0x07: 'de', 0x0a: 'es', 0x16: 'pt', 0x19: 'ru', 0x10: 'it', 0x15: 'pl',
                   0x1f: 'tr', 0x22: 'uk', 0x04: 'zh', 0x11: 'ja', 0x12: 'ko'}
APP_ID = '4262460'

_current = 'fr'
_tables: dict[str, dict[str, str]] = {}
_detected: tuple[str, str] | None = None


def load(data: Path):
    global _tables
    try: _tables = json.loads((data/'i18n.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): logging.exception('Translations unavailable'); _tables = {}


def current() -> str:
    return _current


def set_language(code: str):
    global _current
    _current = code if code in LANGUAGES else 'en'


def _(text: str, **values) -> str:
    """Translation of a French interface text; English when a language lacks it; French as the last resort."""
    if _current != 'fr':
        text = _tables.get(_current, {}).get(text) or _tables.get('en', {}).get(text) or text
    return text.format(**values) if values else text


def plural(count: int, one: str, many: str, **values) -> str:
    """Count-dependent text. Slavic languages have several plural forms: their 'many' texts are written so that
    one form reads well for every number (« Объектов : 5 »)."""
    key = one if count == 1 or (_current == 'fr' and count == 0) else many
    return _(key, n=count, **values)


# ------------------------------------------------------------------ automatic detection
def _game_language():
    """Language chosen in ROB RUN's settings (Unity PlayerPrefs in the registry)."""
    try: import winreg
    except ImportError: return None
    for company in ('Kefir', 'KEFIR'):
        try: key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf'Software\{company}\ROB RUN')
        except OSError: continue
        with key:
            index = 0
            while True:
                try: name, value, _kind = winreg.EnumValue(key, index)
                except OSError: break
                index += 1
                if not re.search(r'locale|language', name, re.I): continue
                if isinstance(value, bytes): value = value.rstrip(b'\0').decode('utf-8', 'ignore')
                code = _code(str(value))
                if code: return code
    return None


def _steam_language():
    """Language set for ROB RUN in Steam (Properties > General > Language)."""
    try:
        from watcher import steam_roots
        roots = list(steam_roots())
    except Exception:
        roots = []
    libraries = []
    for root in roots:
        libraries.append(Path(root))
        try: text = (Path(root)/'steamapps'/'libraryfolders.vdf').read_text(encoding='utf-8', errors='ignore')
        except OSError: continue
        libraries += [Path(p.replace('\\\\', '\\')) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    for library in libraries:
        try: manifest = (library/'steamapps'/f'appmanifest_{APP_ID}.acf').read_text(encoding='utf-8', errors='ignore')
        except OSError: continue
        found = re.search(r'"language"\s+"([^"]+)"', manifest)
        if found and found.group(1).lower() in STEAM_NAMES: return STEAM_NAMES[found.group(1).lower()]
    return None


def _windows_language():
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return WINDOWS_PRIMARY.get(langid & 0x3ff)
    except (AttributeError, OSError):
        pass
    import locale
    return _code(locale.getlocale()[0] or os.environ.get('LANG', ''))


def _code(value: str):
    value = (value or '').strip().lower().replace('_', '-')
    if not value: return None
    if value in STEAM_NAMES: return STEAM_NAMES[value]
    code = value.split('-')[0][:2]
    names = {'french': 'fr', 'english': 'en', 'german': 'de', 'spanish': 'es', 'portuguese': 'pt', 'russian': 'ru'}
    code = names.get(value.split('(')[0].strip(), code)
    return code if code in LANGUAGES else None


def detect() -> tuple[str, str]:
    """(language, where it came from), cached for the session."""
    global _detected
    if _detected is None:
        for source, finder in (('game', _game_language), ('steam', _steam_language), ('windows', _windows_language)):
            try: code = finder()
            except Exception: logging.exception('Language detection (%s) failed', source); code = None
            if code in LANGUAGES:
                _detected = (code, source)
                break
        else:
            _detected = ('en', 'default')
    return _detected


def setup(choice: str | None):
    """'auto' (or nothing) follows the game; otherwise a language code."""
    set_language(choice if choice in LANGUAGES else detect()[0])
    return _current


def item_name(item: dict) -> str:
    names = item.get('names') or {}
    return names.get(_current) or names.get('en') or item.get('name_fr') or item.get('name', '')
