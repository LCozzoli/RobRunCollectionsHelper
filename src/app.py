from __future__ import annotations
import argparse, ctypes, json, logging, os, queue, re, sys, threading, time
from collections import Counter
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont
from PIL import Image, ImageTk, ImageDraw, ImageEnhance
from detector import Detector, COLORS, LABELS
from watcher import FolderWatcher, discover_steam_folder, fingerprint, readable_folder, startup_folder
from cleanup import recycle_capture
from icons import icon
import i18n
from i18n import _, plural, LANGUAGES
from collections_view import (CollectionsView, Tooltip, display_name, recipe_label, collection_title, load_icon, Shapes,
                              mix, items_under_pointer, fit_scrollbar, TIER_NAMES, POIS, CARD, SLOT, LINE, CHIP)

BASE = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA = Path(getattr(sys, '_MEIPASS', BASE))/'data'
BG, PANEL, MUTED, TEXT = '#101318', '#191e27', '#97a4b9', '#eef3fa'
PANEL2, ACCENT, WARN, ERROR, FAINT = '#232c39', '#b5ef83', '#f5c65c', '#ff8d8d', '#5d6877'
TIER_ORDER = ['basic', 'purple', 'gold']
DIM = .5  # brightness of inventory cells that are not worth keeping
SET_ICON, SET_H, SET_GAP = 32, 56, 6
POLL_MS = 100  # folder check interval; a complete JPEG is analysed at first sight
DEFAULTS_VERSION = 2  # bump to apply new default settings once
APP_NAME = 'Collections Helper'


def migrate_settings(settings):
    """Applies new defaults once to older settings; returns (settings, changed)."""
    if not isinstance(settings, dict): settings = {}
    version = settings.get('defaults_version', 1)
    if not isinstance(version, int) or isinstance(version, bool): version = 1
    if version >= DEFAULTS_VERSION: return settings, False
    # v2: « Corbeille auto » on by default. Applied once; a later choice is kept.
    settings.update({'delete_after_analysis': True, 'delete_confirmed': True, 'defaults_version': DEFAULTS_VERSION})
    return settings, True


HELP = (
    'COMMENT ÇA MARCHE\n'
    'Laisse cette fenêtre ouverte. En jeu, ouvre ton sac, un coffre ou le coffre de la voiture '
    '(premier onglet actif), puis appuie sur F12. La capture Steam est analysée automatiquement, '
    'quelle que soit la langue du jeu : le sac et le coffre sont repérés par leurs cases.\n\n'
    'DÉTECTION\n'
    '• Les objets à garder sont encadrés sur la capture ; les autres cases sont assombries.\n'
    '• « Sets en cours » : chaque collection touchée, objets trouvés allumés et manquants estompés, '
    'les plus avancées en premier.\n'
    '• Survole un cadre ou un objet d’un set pour les relier ; clique pour voir ses recettes.\n'
    '• Les pastilles Basique / Purple / Gold et Sac / Coffre masquent ou réaffichent des objets.\n'
    '• Clic droit sur la capture : réanalyser ou régler les zones du sac et du coffre.\n\n'
    'COLLECTIONS\n'
    'Les 34 recettes sur deux pages : Basique (une ligne par lieu du jeu : base 01-07 et '
    'supplémentaire 08-14, 50 000 $), puis Purple (10 spéciales, 150 000 $) et Gold (10 uniques, 500 000 $). '
    'Change de page avec le sélecteur ou les flèches gauche / droite. '
    'Survole un objet pour voir son nom et où le trouver. Clic droit : toutes ses recettes ou sa fiche en ligne. '
    'Une coche marque les objets visibles sur ta dernière capture ; la pastille « Repérées » ne montre que ces '
    'recettes. La recherche accepte le nom dans n’importe quelle langue, sans tenir compte des accents.\n\n'
    'COULEURS\nVert = Basique · Violet = Purple · Or = Gold. La couleur indique la collection, pas la rareté.\n\n'
    'RACCOURCIS\nF5 réanalyser · Ctrl+O analyser une image · Ctrl+F rechercher · Ctrl+1 / Ctrl+2 changer '
    'd’onglet · flèches : page des collections · F1 aide\n\n'
    'Options : dossier des captures, surveillance, Corbeille après analyse, premier plan, zones, langue.\n\n'
    'Tout est traité sur ce PC, sans compte ni envoi.')


def zone_text(label, default='Coffre'):
    """Interface text for a zone label of the detector ('Bag', 'Car trunk', 'Storage')."""
    return _({'Bag': 'Sac', 'Car trunk': 'Coffre voiture', 'Storage': 'Coffre / stockage'}.get(label, default))


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def ago(stamp):
    seconds = max(0, time.time()-stamp)
    if seconds < 60: return _('à l’instant')
    if seconds < 3600: return _('il y a {n} min', n=int(seconds//60))
    if seconds < 86400: return _('il y a {n} h', n=int(seconds//3600))
    return _('il y a {n} j', n=int(seconds//86400))


class Chip(tk.Label):
    """Toggle pill: filled dot on / ring off (same-size icons), with an optional count."""
    def __init__(self, parent, text, var, command, color=TEXT, tooltip=None):
        super().__init__(parent, font=('Segoe UI', 10), padx=12, pady=6, cursor='hand2')
        self.var, self.command, self.color, self.base, self.count = var, command, color, text, None
        self.bind('<Button-1>', self.toggle)
        self.refresh()

    def toggle(self, event=None):
        self.var.set(not self.var.get())
        self.refresh()
        self.command()

    def set_count(self, count):
        self.count = count
        self.refresh()

    def refresh(self):
        on = self.var.get()
        self.state_icon = 'on' if on else 'off'
        text = self.base+('' if self.count is None else f'  {self.count}')
        self.configure(text=text, image=icon(self, self.state_icon, self.color if on else FAINT, 12, gap=2),
                       compound='left', bg='#2a3341' if on else PANEL, fg=self.color if on else FAINT)


class TabBar(tk.Canvas):
    """Segmented tabs for a ttk.Notebook whose own tabs are hidden: antialiased pill, sliding selection,
    hover and an unread badge. The notebook keeps the tab texts and the selection."""
    PAD, RADIUS, BADGE_H = 4, 12, 18
    SELECTED, HOVERED = '#2b3546', PANEL2

    def __init__(self, parent, notebook):
        self.font = tkfont.Font(parent, family='Segoe UI', size=11, weight='bold')
        self.badge_font = tkfont.Font(parent, family='Segoe UI', size=9, weight='bold')
        self.height = self.font.metrics('linespace')+22
        super().__init__(parent, height=self.height, bg=BG, highlightthickness=0, bd=0, cursor='hand2')
        self.notebook = notebook
        self.shapes, self.badges, self.hover, self.anim = Shapes(self), {}, None, None
        self.slide = None  # x of the selection pill, animated between segments
        self.bind('<Motion>', self.motion)
        self.bind('<Leave>', self.leave)
        self.bind('<Button-1>', self.click)
        notebook.bind('<<NotebookTabChanged>>', self.changed, add='+')
        self.refresh()

    def pages(self):
        return [self.notebook.nametowidget(name) for name in self.notebook.tabs()]

    def label(self, page):
        return self.notebook.tab(page, 'text').split('  ●')[0]

    def segment(self):
        # Equal segments wide enough for the longest label plus a badge, so nothing moves when a badge appears.
        widest = max([self.font.measure(self.label(page)) for page in self.pages()] or [60])
        return widest+self.badge_font.measure('88')+12+56

    def selected(self):
        try: return self.notebook.index('current')
        except tk.TclError: return 0

    def target(self, index):
        return self.PAD+index*self.segment()

    def refresh(self):
        count = len(self.pages())
        self.configure(width=2*self.PAD+count*self.segment())
        self.slide = self.target(self.selected())
        self.draw()

    def set_badge(self, page, text):
        """None hides the badge; '' shows a dot; any other text is shown in an accent pill."""
        if text is None: self.badges.pop(str(page), None)
        else: self.badges[str(page)] = text
        self.draw()

    def changed(self, event=None):
        root = self.winfo_toplevel()  # callbacks owned by the window, which cancels them all on close
        if self.anim: root.after_cancel(self.anim)
        start, end = self.slide if self.slide is not None else self.target(self.selected()), self.target(self.selected())
        def step(frame, frames=8):
            t = frame/frames
            self.slide = start+(end-start)*(1-(1-t)**3)  # ease-out
            self.draw()
            self.anim = root.after(14, step, frame+1) if frame < frames else None
        step(1)

    def index_at(self, x):
        index = int((x-self.PAD)//self.segment())
        return index if 0 <= index < len(self.pages()) and self.PAD <= x else None

    def motion(self, event):
        index = self.index_at(event.x)
        if index != self.hover:
            self.hover = index
            self.draw()

    def leave(self, event=None):
        self.hover = None
        self.draw()

    def click(self, event):
        index = self.index_at(event.x)
        if index is not None and index != self.selected():
            self.notebook.select(self.pages()[index])

    def draw(self):
        self.delete('all')
        pages, segment, selected = self.pages(), self.segment(), self.selected()
        width, height, pad = 2*self.PAD+len(pages)*segment, self.height, self.PAD
        self.shapes.rect(self, 0, 0, width, height, self.RADIUS, PANEL, '#283140', 1)
        if self.hover is not None and self.hover != selected:
            x = self.target(self.hover)
            self.shapes.rect(self, x, pad, x+segment, height-pad, self.RADIUS-pad, self.HOVERED)
        x = self.slide if self.slide is not None else self.target(selected)
        self.shapes.rect(self, x, pad, x+segment, height-pad, self.RADIUS-pad, self.SELECTED, '#3a4659', 1)
        for index, page in enumerate(pages):
            text, badge = self.label(page), self.badges.get(str(page))
            badge_w = 0 if badge is None else max(8, self.badge_font.measure(badge)+12) if badge else 8
            gap = 8 if badge is not None else 0
            left = self.target(index)+(segment-self.font.measure(text)-gap-badge_w)/2
            color = ACCENT if index == selected else TEXT if index == self.hover else MUTED
            self.create_text(left, height/2, text=text, anchor='w', fill=color, font=self.font, tags=('label',))
            if badge is not None:
                bx = left+self.font.measure(text)+gap
                bh = self.BADGE_H if badge else 8
                self.shapes.rect(self, bx, (height-bh)/2, bx+badge_w, (height+bh)/2, bh/2, ACCENT, tags=('badge',))
                if badge:
                    self.create_text(bx+badge_w/2, height/2, text=badge, fill=BG, font=self.badge_font, tags=('badge',))


class App(tk.Tk):
    def __init__(self, folder=None):
        super().__init__()
        self.title(APP_NAME)
        self.geometry('1120x780')
        self.minsize(900, 640)
        self.configure(bg=BG)
        try: self.iconbitmap(str(DATA/'app.ico'))
        except tk.TclError: pass
        self.config_path = BASE/'settings.json'
        try: self.settings = json.loads(self.config_path.read_text(encoding='utf-8'))
        except (OSError, ValueError): self.settings = {}
        self.settings, migrated = migrate_settings(self.settings)
        i18n.load(DATA)
        i18n.setup(self.settings.get('language', 'auto'))
        self.language = tk.StringVar(value=self.settings.get('language', 'auto'))
        if migrated:
            try: write_json(self.config_path, self.settings)
            except OSError: logging.exception('Could not save settings')
        self.restore_geometry()
        self.detector = None
        self.jobs, self.events = queue.Queue(maxsize=1), queue.Queue()
        self.closed = threading.Event()
        self.generation = 0
        self.analysis = None
        self.analyzing = False
        self.catalog_ready = False
        self.current_path = None
        self.current_image = None
        self.watcher = None
        self.folder_mode = self.settings.get('folder_mode', 'auto')
        self.images = []
        self.small_icons = {}
        self.set_slots = {}
        self.set_order = []
        self.hover_key = None
        self.unseen = 0
        self.folder_error = False
        self.caption_extra = ''
        self.failures = Counter()
        self.manual_zone = self.settings.get('zone')
        self.manual_trunk = self.settings.get('zone_trunk', self.settings.get('zone_coffre'))  # 'zone_coffre' before 1.2
        self.filters = {k: tk.BooleanVar(value=True) for k in COLORS}
        self.locations = {k: tk.BooleanVar(value=True) for k in ('bag', 'trunk')}
        self.top = tk.BooleanVar(value=self.settings.get('topmost', False))
        self.zoom = tk.BooleanVar(value=self.settings.get('zoom', True))
        self.watch_active = tk.BooleanVar(value=True)
        self.delete_after = tk.BooleanVar(value=self.settings.get('delete_after_analysis', True))
        self.tooltip = Tooltip(self)
        self._build()
        self.after(10, self.style_titlebar)
        self.attributes('-topmost', self.top.get())
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.worker_thread = threading.Thread(target=self.worker, name='Recognition', daemon=True)
        self.worker_thread.start()
        chosen, mode = startup_folder(folder, self.settings.get('folder'), self.folder_mode)
        if chosen: self.set_folder(chosen, mode)
        else: self.after(200, self.request_startup_folder)
        self.update_source()
        self.after(150, self.pump)
        self.after(POLL_MS, self.poll)
        self.after(30000, self.tick)

    # ------------------------------------------------------------------ widgets
    def label(self, parent, text='', **kw):
        options = dict(bg=parent.cget('bg'), fg=TEXT, font=('Segoe UI', 10), anchor='w')
        options.update(kw)
        return tk.Label(parent, text=text, **options)

    def button(self, parent, text, command, accent=False):
        return tk.Button(parent, text=text, command=command, bg=ACCENT if accent else '#293242',
                         fg='#152012' if accent else TEXT, activebackground='#40506a', activeforeground=TEXT,
                         relief='flat', bd=0, highlightthickness=0, padx=13, pady=8, cursor='hand2',
                         font=('Segoe UI', 10))

    def link(self, parent, text, command, **kw):
        options = dict(fg=ACCENT, cursor='hand2', font=('Segoe UI', 10, 'underline'))
        options.update(kw)
        widget = self.label(parent, text, **options)
        widget.bind('<Button-1>', lambda event: command())
        return widget

    def check(self, parent, text, var, command, color=TEXT):
        return tk.Checkbutton(parent, text=text, variable=var, command=command, bg=parent.cget('bg'), fg=color,
                              activebackground=parent.cget('bg'), activeforeground=color, selectcolor='#293242',
                              font=('Segoe UI', 10), relief='flat', highlightthickness=0, cursor='hand2')

    def hint(self, widget, text):
        widget.bind('<Enter>', lambda event: self.tooltip.show(event.x_root, event.y_root, text), add='+')
        widget.bind('<Leave>', lambda event: self.tooltip.hide(), add='+')

    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill='x', padx=24, pady=(16, 8))
        try:
            self.logo = ImageTk.PhotoImage(Image.open(DATA/'logo.png').resize((38, 38), Image.LANCZOS), master=self)
            tk.Label(header, image=self.logo, bg=BG, bd=0).pack(side='left', padx=(0, 12))
        except (OSError, tk.TclError):
            logging.exception('Logo missing')
        self.label(header, 'Collections Helper', font=('Segoe UI', 21, 'bold')).pack(side='left')
        self.options_button = tk.Menubutton(header, text=_('Options'), image=icon(self, 'gear', TEXT, 15, gap=3), compound='left', bg='#293242', fg=TEXT, activebackground='#40506a',
                                            activeforeground=TEXT, relief='flat', bd=0, highlightthickness=0,
                                            padx=14, pady=8, cursor='hand2', font=('Segoe UI', 10))
        self.options_menu = self.build_options_menu(self.options_button)
        self.options_button.configure(menu=self.options_menu)
        self.options_button.pack(side='right')  # help stays in Options › Aide and on F1
        self.top_chip = Chip(header, _('Au premier plan'), self.top, self.toggle_top)
        self.top_chip.pack(side='right', padx=(0, 8))
        self.hint(self.top_chip, _('Garde cette fenêtre au-dessus du jeu\n(jeu en mode fenêtré ou sans bordure).'))

        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('Collections.TNotebook', background=BG, borderwidth=0, bordercolor=BG, lightcolor=BG,
                        darkcolor=BG, tabmargins=(0, 0, 0, 0))
        style.layout('Collections.TNotebook.Tab', [])  # the TabBar above draws the tabs
        style.configure('Vertical.TScrollbar', background='#293242', troughcolor=BG, bordercolor=BG,
                        lightcolor='#293242', darkcolor='#293242', arrowcolor=MUTED)
        style.map('Vertical.TScrollbar', background=[('active', '#40506a')])
        tab_row = tk.Frame(self, bg=BG)
        tab_row.pack(fill='x', padx=24, pady=(0, 8))
        self.tabs = ttk.Notebook(self, style='Collections.TNotebook')
        self.tabs.pack(fill='both', expand=True, padx=24, pady=(0, 6))
        self.detection_tab = tk.Frame(self.tabs, bg=BG)
        self.tabs.add(self.detection_tab, text=_('Détection'))
        self.collections_view = CollectionsView(self.tabs, DATA)
        self.tabs.add(self.collections_view, text=_('Collections'))
        self.tab_bar = TabBar(tab_row, self.tabs)
        self.tab_bar.pack(side='left')
        self._build_detection(self.detection_tab)
        self.footer_bar = footer = tk.Frame(self, bg=BG)
        footer.pack(fill='x', padx=24, pady=(0, 10))
        self.footer = self.label(footer, '', fg=FAINT, font=('Segoe UI', 9))
        self.footer.pack(side='left')
        self.label(footer, _('Analyse locale · aucune capture envoyée'), fg=FAINT, font=('Segoe UI', 9)).pack(side='right')
        self.tabs.bind('<<NotebookTabChanged>>', self.tab_changed, add='+')
        self.bind_all('<MouseWheel>', self.scroll_cards)
        self.bind('<F1>', lambda event: self.help())
        self.bind('<F5>', lambda event: self.reanalyze())
        self.bind('<Control-o>', lambda event: self.open_image())
        self.bind('<Control-Key-1>', lambda event: self.tabs.select(self.detection_tab))
        self.bind('<Control-Key-2>', lambda event: self.tabs.select(self.collections_view))
        self.bind('<Control-f>', lambda event: self.find_collection())
        for key, step in [('<Left>', -1), ('<Right>', 1), ('<Prior>', -1), ('<Next>', 1)]:
            self.bind(key, lambda event, s=step: self.page_key(event, s))
        self.tab_changed()

    def build_options_menu(self, parent):
        menu = tk.Menu(parent, tearoff=False, bg=PANEL, fg=TEXT, activebackground='#344152', activeforeground=TEXT,
                       selectcolor=ACCENT, font=('Segoe UI', 10), bd=0)
        menu.add_command(label=_('Réanalyser la dernière capture'), accelerator='F5', command=self.reanalyze)
        menu.add_command(label=_('Analyser une autre image…'), accelerator='Ctrl+O', command=self.open_image)
        menu.add_separator()
        menu.add_command(label=_('Changer de dossier de captures…'), command=self.choose_folder)
        menu.add_command(label=_('Retrouver le dossier Steam automatiquement'), command=self.auto_folder)
        menu.add_command(label=_('Ouvrir le dossier des captures'), command=self.open_folder)
        menu.add_separator()
        menu.add_command(label=_('Régler la zone du sac…'), command=lambda: self.choose_zone('bag'))
        menu.add_command(label=_('Régler la zone du coffre…'), command=lambda: self.choose_zone('trunk'))
        menu.add_command(label=_('Rétablir les zones automatiques'), command=self.reset_zone)
        menu.add_separator()
        menu.add_checkbutton(label=_('Surveiller le dossier'), variable=self.watch_active, command=self.update_source)
        menu.add_checkbutton(label=_('Mettre les captures analysées à la Corbeille'), variable=self.delete_after,
                             command=self.toggle_delete)
        menu.add_checkbutton(label=_('Zoom sur les inventaires'), variable=self.zoom, command=self.toggle_zoom)
        menu.add_checkbutton(label=_('Garder la fenêtre au premier plan'), variable=self.top, command=self.toggle_top)
        menu.add_separator()
        languages = tk.Menu(menu, tearoff=False, bg=PANEL, fg=TEXT, activebackground='#344152', activeforeground=TEXT,
                            selectcolor=ACCENT, font=('Segoe UI', 10), bd=0)
        detected = LANGUAGES[i18n.detect()[0]]
        languages.add_radiobutton(label=_('Automatique ({language})', language=detected), value='auto',
                                  variable=self.language, command=self.change_language)
        languages.add_separator()
        for code, name in LANGUAGES.items():
            languages.add_radiobutton(label=name, value=code, variable=self.language, command=self.change_language)
        menu.add_cascade(label=_('Langue'), menu=languages)
        menu.add_separator()
        menu.add_command(label=_('Aide'), accelerator='F1', command=self.help)
        return menu

    def change_language(self):
        """Saves the choice and reopens the window in the new language (settings and captures are kept)."""
        choice = self.language.get()
        if choice == self.settings.get('language', 'auto'): return
        self.settings['language'] = choice
        self.save_settings()
        i18n.setup(choice)
        if not messagebox.askyesno(_('Langue'), _('Relancer maintenant pour appliquer la langue ?'), parent=self): return
        import subprocess
        command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]
        # A one-file executable must start as a fresh instance: inheriting PyInstaller's variables would make the
        # new window use this instance's temporary folder, which is deleted when this window closes.
        env = {k: v for k, v in os.environ.items() if not k.startswith(('_MEI', '_PYI', 'TCL_LIBRARY', 'TK_LIBRARY'))}
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        try: subprocess.Popen(command, cwd=str(BASE), env=env)
        except OSError: logging.exception('Could not restart'); return
        self.close()

    def _build_detection(self, parent):
        # Source: where captures come from and whether we are watching.
        source = tk.Frame(parent, bg=PANEL)
        source.pack(fill='x', pady=(14, 10))
        self.label(source, _('Captures'), fg=MUTED, font=('Segoe UI', 9, 'bold'), padx=14, pady=10).pack(side='left')
        self.folder_text = self.label(source, _('Recherche du dossier…'), font=('Segoe UI', 10), cursor='hand2')
        self.folder_text.pack(side='left')
        self.folder_text.bind('<Button-1>', lambda event: self.choose_folder() if self.watcher else self.request_startup_folder())
        self.folder_hint = ''
        self.folder_text.bind('<Enter>', lambda event: self.tooltip.show(event.x_root, event.y_root, self.folder_hint))
        self.folder_text.bind('<Leave>', lambda event: self.tooltip.hide())
        folder_menu = tk.Menu(self, tearoff=False)
        folder_menu.add_command(label=_('Choisir le dossier…'), command=self.choose_folder)
        folder_menu.add_command(label=_('Détection automatique'), command=self.auto_folder)
        folder_menu.add_command(label=_('Ouvrir le dossier'), command=self.open_folder)
        self.folder_text.bind('<Button-3>', lambda event: self.popup_menu(folder_menu, event))
        self.link(source, _('Changer'), self.choose_folder, padx=10, font=('Segoe UI', 9, 'underline')).pack(side='left')
        self.monitor = self.label(source, '', font=('Segoe UI', 9, 'bold'), padx=14, cursor='hand2')
        self.monitor.pack(side='right')
        self.monitor.bind('<Button-1>', lambda event: self.toggle_watch())
        self.hint(self.monitor, _('Cliquer pour suspendre ou reprendre la surveillance.'))
        # Toggle, just left of the watch status: Corbeille auto, dot when on, ring when off.
        self.recycle_chip = Chip(source, _('Corbeille auto'), self.delete_after, self.toggle_delete, WARN)
        self.recycle_chip.configure(font=('Segoe UI', 9, 'bold'), pady=4)
        self.recycle_chip.pack(side='right', padx=(0, 4))
        self.hint(self.recycle_chip, _('Activée : chaque capture analysée est déplacée dans la Corbeille Windows\n'
                                     '(récupérable tant que la Corbeille n’est pas vidée). Clic : activer / désactiver.'))

        # Result summary and the filters, as toggle chips with counts.
        summary = tk.Frame(parent, bg=BG)
        summary.pack(fill='x', pady=(0, 10))
        self.status = self.label(summary, _('Chargement du catalogue…'), fg=ACCENT, font=('Segoe UI', 13, 'bold'))
        self.status.pack(side='left')
        controls = tk.Frame(parent, bg=BG)
        controls.pack(fill='x', pady=(0, 10))
        self.tier_chips = {}
        for key in COLORS:
            chip = Chip(controls, _(TIER_NAMES[key]), self.filters[key], self.refresh_results, COLORS[key])
            chip.pack(side='left', padx=(0, 6))
            self.tier_chips[key] = chip
        tk.Frame(controls, bg=BG, width=18).pack(side='left')
        self.location_chips = {}
        for key, title in [('bag', _('Sac')), ('trunk', _('Coffre'))]:
            chip = Chip(controls, title, self.locations[key], self.refresh_results)
            chip.pack(side='left', padx=(0, 6))
            self.location_chips[key] = chip
        self.zoom_chip = Chip(controls, _('Zoom'), self.zoom, self.toggle_zoom)
        self.zoom_chip.pack(side='right')
        self.hint(self.zoom_chip, _('Agrandit les inventaires, ou affiche la capture entière.'))

        body = tk.Frame(parent, bg=BG)
        body.pack(fill='both', expand=True)
        preview = tk.Frame(body, bg=PANEL)
        preview.pack(side='left', fill='both', expand=True, padx=(0, 14))
        self.canvas = tk.Canvas(preview, bg='#141922', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', self.preview_resized)
        self.canvas.bind('<Motion>', self.preview_motion)
        self.canvas.bind('<Leave>', lambda event: self.after(80, self.preview_check_leave))
        self.canvas.bind('<Button-1>', self.preview_click)
        zone_menu = tk.Menu(self, tearoff=False)
        zone_menu.add_command(label=_('Réanalyser'), command=self.reanalyze)
        zone_menu.add_separator()
        zone_menu.add_command(label=_('Définir la zone du sac…'), command=lambda: self.choose_zone('bag'))
        zone_menu.add_command(label=_('Définir la zone du coffre…'), command=lambda: self.choose_zone('trunk'))
        zone_menu.add_command(label=_('Rétablir les zones automatiques'), command=self.reset_zone)
        self.canvas.bind('<Button-3>', lambda event: self.popup_menu(zone_menu, event))
        self.preview_after = None
        self.canvas_targets = []
        self.caption = self.label(preview, '', fg=MUTED, padx=12, pady=8, font=('Segoe UI', 9))
        self.caption.pack(fill='x')

        sidebar = tk.Frame(body, bg=BG, width=344)
        sidebar.pack(side='right', fill='y')
        sidebar.pack_propagate(False)
        self.count_label = self.label(sidebar, _('SETS EN COURS'), font=('Segoe UI', 11, 'bold'))
        self.count_label.pack(fill='x')
        self.sets_hint = self.label(sidebar, '', fg=MUTED, font=('Segoe UI', 8))
        self.sets_hint.pack(fill='x', pady=(0, 8))
        self.cards_canvas = tk.Canvas(sidebar, bg=BG, highlightthickness=0)
        self.sets_scrollbar = scrollbar = ttk.Scrollbar(sidebar, orient='vertical', command=self.cards_canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.cards_canvas.pack(side='left', fill='both', expand=True)
        self.cards_canvas.configure(yscrollcommand=scrollbar.set)
        self.sets_width = 0
        self.cards_canvas.bind('<Configure>', self.sets_resized)
        self.cards_canvas.bind('<Leave>', lambda e: self.after(80, self.slot_check_leave))
        self.refresh_results()

    # ------------------------------------------------------------------ helpers
    def popup_menu(self, menu, event):
        try: menu.tk_popup(event.x_root, event.y_root)
        finally: menu.grab_release()

    @staticmethod
    def parse_geometry(geometry):
        """'WxH+X+Y' as returned by Tk, where X/Y may be negative ('+-1225') on a monitor left of the main one."""
        match = re.fullmatch(r'(\d+)x(\d+)([+-])(-?\d+)([+-])(-?\d+)', str(geometry or '').strip())
        if not match: return None
        width, height = int(match.group(1)), int(match.group(2))
        if match.group(3) != '+' or match.group(5) != '+':
            return width, height, None, None  # offsets from the right/bottom edge: keep only the size
        return width, height, int(match.group(4)), int(match.group(6))

    def desktop_bounds(self):
        """Whole desktop across all monitors (x, y, width, height)."""
        try:
            metrics = ctypes.windll.user32.GetSystemMetrics
            bounds = metrics(76), metrics(77), metrics(78), metrics(79)
            if bounds[2] > 0 and bounds[3] > 0: return bounds
        except (AttributeError, OSError):
            pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def restore_geometry(self):
        try:
            parsed = self.parse_geometry(self.settings.get('geometry'))
            if not parsed: return
            width, height, x, y = parsed
            left, top, desk_w, desk_h = self.desktop_bounds()
            width, height = max(900, min(width, desk_w)), max(640, min(height, desk_h))
            visible = (x is not None and left-50 <= x <= left+desk_w-200 and top <= y <= top+desk_h-150)
            self.geometry(f'{width}x{height}+{x}+{y}' if visible else f'{width}x{height}')
            if self.settings.get('maximized'): self.state('zoomed')
        except (tk.TclError, ValueError, TypeError):
            logging.exception('Saved window position ignored')  # never block startup over a saved position

    def style_titlebar(self, window=None):
        """Windows 10/11: dark title bar in the app's colours, rounded corners. Ignored elsewhere."""
        window = window or self
        try:
            if not window.winfo_exists(): return
            dwm, hwnd = ctypes.windll.dwmapi, ctypes.windll.user32.GetParent(window.winfo_id())
        except (AttributeError, OSError):
            return
        def colorref(color):
            r, g, b = (int(color[i:i+2], 16) for i in (1, 3, 5))
            return r | g << 8 | b << 16
        for attribute, value in [(20, 1), (19, 1), (33, 2), (35, colorref(BG)), (36, colorref(TEXT)),
                                 (34, colorref('#283140'))]:
            # 20/19 dark mode (Windows 10 2004+ / older 10), 33 rounded corners, 35-36 caption colours, 34 border (11)
            value = ctypes.c_int(value)
            try: dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
            except OSError: pass

    def tab_changed(self, event=None):
        if self.tabs.select() == str(self.collections_view):
            # Collections uses the whole height: no hint line at the bottom.
            self.footer_bar.pack_forget()
            self.collections_view.show()
        else:
            self.unseen = 0
            self.tabs.tab(self.detection_tab, text=_('Détection'))
            self.tab_bar.set_badge(self.detection_tab, None)
            if self.footer_bar.winfo_manager() != 'pack':
                self.footer_bar.pack(fill='x', padx=24, pady=(0, 10))
            self.draw_preview()
            self.footer.configure(text=_('Survole un cadre pour identifier l’objet  ·  Clic : voir ses recettes  ·  '
                                       'F5 : réanalyser  ·  F1 : aide'))
        self.tooltip.hide()
        self.collections_view.leave_item()

    def page_key(self, event, step):
        # Arrows still move the cursor while typing in the search field.
        if self.tabs.select() != str(self.collections_view) or event.widget is self.collections_view.search: return
        self.collections_view.turn_page(step)
        return 'break'

    def find_collection(self):
        self.tabs.select(self.collections_view)
        self.collections_view.focus_search()

    def scroll_cards(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget in (self.cards_canvas, self.collections_view.canvas):
                widget.yview_scroll(int(-event.delta/120), 'units')
                return 'break'
            widget = getattr(widget, 'master', None)

    def preview_resized(self, event):
        if self.preview_after: self.after_cancel(self.preview_after)
        self.preview_after = self.after(100, self.draw_preview)

    def save_settings(self):
        self.settings.setdefault('language', self.language.get() if hasattr(self, 'language') else 'auto')
        self.settings.pop('zone_coffre', None)
        self.settings.update({'folder': str(self.watcher.folder) if self.watcher else '', 'folder_mode': self.folder_mode,
                              'delete_after_analysis': self.delete_after.get(), 'topmost': self.top.get(),
                              'zone': self.manual_zone, 'zone_trunk': self.manual_trunk, 'zoom': self.zoom.get()})
        try: write_json(self.config_path, self.settings)
        except OSError: logging.exception('Could not save settings')

    def toggle_top(self):
        self.attributes('-topmost', self.top.get())
        self.top_chip.refresh()
        self.save_settings()

    def toggle_zoom(self):
        self.zoom_chip.refresh()
        self.save_settings()
        self.draw_preview()

    def toggle_watch(self):
        self.watch_active.set(not self.watch_active.get())
        self.update_source()

    def toggle_delete(self):
        # Confirmed once; afterwards the toggle switches directly.
        if self.delete_after.get() and not self.settings.get('delete_confirmed'):
            accepted = messagebox.askyesno(
                _('Mettre les captures à la Corbeille ?'),
                _('Après chaque analyse réussie, la capture du dossier surveillé sera déplacée dans la Corbeille '
                'Windows (récupérable tant que la Corbeille n’est pas vidée).\n\n'
                'Les images ouvertes depuis un autre dossier ne sont jamais supprimées.\n\nActiver cette option ?'),
                parent=self)
            if accepted: self.settings['delete_confirmed'] = True
            else: self.delete_after.set(False)
        self.save_settings()
        self.update_source()

    def disable_delete(self):
        self.delete_after.set(False)
        self.save_settings()
        self.update_source()

    def update_source(self):
        if not hasattr(self, 'monitor'): return
        if self.watcher is None:
            self.folder_text.configure(text=_('Pas encore de dossier — fais une capture avec F12 dans ROB RUN'), fg=WARN)
            self.folder_hint = _('Steam crée ce dossier à ta première capture avec F12. Clic : marche à suivre.')
        else:
            folder = self.watcher.folder
            steam = 'userdata' in folder.parts and '4262460' in folder.parts
            name = _('Captures Steam de ROB RUN') if steam else folder.name or str(folder)
            how = _('détecté automatiquement') if self.folder_mode == 'auto' else _('choisi par toi')
            self.folder_text.configure(text=f'{name}  ·  {how}', fg=TEXT)
            self.folder_hint = f'{folder}\n\n'+_('Clic : choisir un autre dossier  ·  Clic droit : plus d’options')
        self.recycle_chip.refresh()
        if self.watcher is None:
            state, text, color = 'off', _('AUCUN DOSSIER'), WARN
        elif not self.watch_active.get():
            state, text, color = 'pause', _('SURVEILLANCE EN PAUSE'), MUTED
        elif getattr(self, 'folder_error', False):
            state, text, color = 'warn', _('DOSSIER INACCESSIBLE'), WARN
        else:
            state, text, color = 'on', _('SURVEILLANCE ACTIVE'), ACCENT
        self.monitor.configure(text=text, fg=color, image=icon(self, state, color, 12, gap=2), compound='left')
        self.monitor_state = state

    def set_status(self, text, color=ACCENT):
        self.status.configure(text=text, fg=color)

    def tick(self):
        # Keep "il y a 3 min" up to date.
        if self.closed.is_set(): return
        self.update_caption()
        self.after(30000, self.tick)

    def update_caption(self, extra=None):
        if extra is not None: self.caption_extra = extra
        extra = getattr(self, 'caption_extra', '')
        if self.analysis is None or self.current_path is None:
            return
        try:
            stamp = self.current_path.stat().st_mtime
            when = datetime.fromtimestamp(stamp)
            date = _('Capture du {day} à {hour}', day=f'{when:%d/%m}', hour=f'{when:%H:%M}')+f'  ·  {ago(stamp)}'
            if time.time()-stamp > 300: date += _(' (ancienne capture)')
        except OSError:
            date = _('Capture conservée en mémoire')
        seconds = f'{self.analysis.elapsed:.1f}'.replace('.', ',' if i18n.current() in ('fr', 'de', 'es', 'pt', 'ru', 'it', 'pl', 'tr', 'uk') else '.')
        self.caption.configure(text=f'{date}  ·  '+_('analysée en {s} s', s=seconds)+extra)

    # ------------------------------------------------------------------ folder
    def set_folder(self, folder, mode='manual'):
        folder = readable_folder(folder)
        if folder is None: return False
        self.folder_mode = mode
        previous = self.settings.get('recycled_captures', {}).get(str(folder))
        self.watcher = FolderWatcher(folder, after=previous)
        self.folder_error = False
        self.update_source()
        self.save_settings()
        return True

    def request_startup_folder(self):
        """No ROB RUN screenshot folder yet: Steam creates it at the first F12 in the game. Explain that, then look
        again when the player says they took one; only if it is still missing, ask for the folder."""
        if self.closed.is_set(): return
        self.set_status(_('Dossier des captures introuvable · fais une capture avec F12 dans ROB RUN.'), WARN)
        self.update_source()
        self.draw_preview()
        if getattr(self, 'first_capture', None) and self.first_capture.winfo_exists():
            self.first_capture.deiconify(); self.first_capture.lift()
            return
        # Lighter card with a warm frame and accent stripe, so it stands out from the dark main window.
        CARD_BG = '#252e3c'
        dialog = self.first_capture = tk.Toplevel(self, bg=WARN, padx=2, pady=2)
        dialog.title(_('Première capture'))
        dialog.transient(self)
        dialog.resizable(False, False)
        tk.Frame(dialog, bg=WARN, height=4).pack(fill='x')
        body = tk.Frame(dialog, bg=CARD_BG, padx=26, pady=20)
        body.pack(fill='both', expand=True)
        head = tk.Frame(body, bg=CARD_BG)
        head.pack(fill='x')
        tk.Label(head, image=icon(self, 'warn', WARN, 20, gap=10), bg=CARD_BG).pack(side='left')
        tk.Label(head, text=_('Première capture'), bg=CARD_BG, fg=TEXT, font=('Segoe UI', 15, 'bold'),
                 anchor='w').pack(side='left')
        tk.Label(body, text=_('Steam crée le dossier des captures de ROB RUN à ta première capture.\n\n'
                              '1. En jeu, appuie sur F12 (touche de capture de Steam).\n'
                              '2. Reviens ici et clique sur « J’ai fait une capture ».'),
                 bg=CARD_BG, fg=TEXT, font=('Segoe UI', 10), justify='left', anchor='w', wraplength=420).pack(fill='x', pady=(12, 0))
        note = tk.Label(body, text='', bg=CARD_BG, fg=WARN, font=('Segoe UI', 9, 'bold'), justify='left', anchor='w',
                        wraplength=420)
        note.pack(fill='x', pady=(8, 0))
        buttons = tk.Frame(body, bg=CARD_BG)
        buttons.pack(fill='x', pady=(14, 0))

        def done():
            note.configure(text=_('Toujours introuvable automatiquement · choisis le dossier où tes captures sont enregistrées.'))
            self.first_capture_done(parent=dialog)

        tk.Button(buttons, text=_('J’ai fait une capture'), command=done, bg=ACCENT, fg=BG, activebackground='#c9f6a2',
                  activeforeground=BG, relief='flat', bd=0, padx=16, pady=7, cursor='hand2',
                  font=('Segoe UI', 10, 'bold')).pack(side='left')
        tk.Button(buttons, text=_('Plus tard'), command=dialog.destroy, bg='#3a4659', fg=TEXT, activebackground='#4a5870',
                  activeforeground=TEXT, relief='flat', bd=0, padx=14, pady=7, cursor='hand2',
                  font=('Segoe UI', 10)).pack(side='left', padx=(8, 0))
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        self.after(10, lambda: self.style_titlebar(dialog))
        dialog.update_idletasks()
        x = self.winfo_rootx()+(self.winfo_width()-dialog.winfo_reqwidth())//2
        y = self.winfo_rooty()+max(40, (self.winfo_height()-dialog.winfo_reqheight())//3)
        dialog.geometry(f'+{max(0, x)}+{max(0, y)}')
        self.done_first_capture = done  # for tests

    def first_capture_done(self, parent=None):
        """« J’ai fait une capture »: look for Steam's folder again; if still missing (other screenshot key or
        tool), the player chooses it."""
        dialog = getattr(self, 'first_capture', None)
        folder = discover_steam_folder()
        if folder: self.change_folder(folder, 'auto')
        else: self.choose_folder(parent=parent)
        if self.watcher and dialog is not None and dialog.winfo_exists(): dialog.destroy()

    def change_folder(self, folder, mode):
        if not self.set_folder(folder, mode):
            self.set_status(_('Ce dossier est inaccessible · choisis-en un autre.'), WARN)
            return
        self.generation += 1
        self.analysis = None
        self.analyzing = False
        self.current_path = None
        self.current_image = None
        self.manual_zone = None
        self.manual_trunk = None
        self.caption_extra = ''
        self.caption.configure(text='')
        self.collections_view.set_found({})
        self.save_settings()
        self.refresh_results()
        self.set_status(_('Dossier prêt · prends une capture avec F12 en jeu.'))

    def auto_folder(self):
        folder = discover_steam_folder()
        if folder: self.change_folder(folder, 'auto')
        else: self.request_startup_folder()

    def choose_folder(self, parent=None):
        folder = filedialog.askdirectory(title=_('Dossier des captures ROB RUN'), parent=parent or self,
                                         initialdir=str(self.watcher.folder) if self.watcher else str(BASE))
        if folder: self.change_folder(Path(folder), 'manual')

    def open_folder(self):
        if not self.watcher:
            self.choose_folder()
            return
        try: os.startfile(str(self.watcher.folder))
        except (AttributeError, OSError) as error:
            messagebox.showinfo(_('Dossier des captures'), str(self.watcher.folder), parent=self)

    def open_image(self):
        path = filedialog.askopenfilename(title=_('Analyser une capture'), filetypes=[(_('Images'), '*.jpg *.jpeg *.png *.webp')])
        if path:
            self.tabs.select(self.detection_tab)
            self.submit(Path(path))

    def reanalyze(self):
        try:
            latest = self.watcher.latest() if self.watcher else None
            if latest and not self.watcher.is_older(latest): self.submit(Path(latest[0]))
            elif self.current_path: self.submit(self.current_path)
            else: self.set_status(_('Aucune capture à analyser · fais F12 en jeu.'), WARN)
        except OSError as error:
            self.set_status(_('Dossier inaccessible : {error}', error=error.strerror or error), WARN)

    def poll(self):
        if self.closed.is_set(): return
        if self.watcher and self.watch_active.get():
            try:
                new = self.watcher.poll()
                if self.folder_error:
                    self.folder_error = False
                    self.update_source()
                if new: self.submit(new)
            except OSError:
                if not self.folder_error:
                    self.folder_error = True
                    self.update_source()
        self.after(POLL_MS, self.poll)

    # ------------------------------------------------------------------ analysis
    def submit(self, path):
        path = Path(path).resolve()
        # A recycled screenshot remains usable for filters and manual grid setup.
        cached = self.current_image.copy() if path == self.current_path and self.current_image is not None and not path.exists() else None
        if path != self.current_path: self.current_image = None
        cleanup_folder = self.watcher.folder if self.delete_after.get() and self.watcher and path.parent == self.watcher.folder and cached is None else None
        self.generation += 1
        self.current_path = path
        self.analysis = None
        self.analyzing = True
        self.caption_extra = ''
        self.refresh_results()
        self.set_status(_('Analyse de la capture…'))
        self.caption.configure(text=path.name)
        job = (self.generation, path, list(self.manual_zone) if self.manual_zone else None, list(self.manual_trunk) if self.manual_trunk else None, cached, cleanup_folder)
        try: self.jobs.get_nowait()
        except queue.Empty: pass
        self.jobs.put_nowait(job)

    def worker(self):
        try:
            self.detector = Detector(DATA)
            self.events.put(('ready', None))
        except Exception as error:
            logging.exception('Catalogue loading failed')
            self.events.put(('fatal', str(error)))
            return
        while not self.closed.is_set():
            try: generation, path, zone, trunk, cached, cleanup_folder = self.jobs.get(timeout=.3)
            except queue.Empty: continue
            for attempt in range(4):
                try:
                    before = fingerprint(path) if cached is None else None
                    result = self.detector.analyze(path if cached is None else cached, zone, trunk)
                    if cached is None and fingerprint(path) != before:
                        raise OSError(_('La capture est encore en cours d’écriture.'))
                    self.events.put(('result', (generation, path, result, before, cleanup_folder)))
                    break
                except (OSError, ValueError) as error:
                    if attempt < 3:
                        if self.closed.wait(.5*(attempt+1)): return
                    else:
                        logging.exception('Could not read %s', path)
                        self.events.put(('error', (generation, str(error))))
                except Exception as error:
                    logging.exception('Analysis of %s failed', path)
                    self.events.put(('error', (generation, str(error))))
                    break

    def result_status(self, result):
        if not result.inventory_box:
            return (_('Inventaire non repéré · ouvre ton sac ou un coffre avant F12 '
                    '(voiture : premier onglet actif).'), WARN)
        total = len({(d.item['id'], d.location) for d in result.detections})
        if not total:
            return _('Aucun objet de collection sur cette capture.'), MUTED
        return plural(total, '{n} objet de collection à garder', '{n} objets de collection à garder'), ACCENT

    def pump(self):
        if self.closed.is_set(): return
        while True:
            try: kind, value = self.events.get_nowait()
            except queue.Empty: break
            if kind == 'fatal':
                self.set_status(_('Catalogue indisponible.'), ERROR)
                messagebox.showerror(_('Chargement impossible'), value)
            elif kind == 'ready':
                self.catalog_ready = True
                if not self.current_path:
                    if self.watcher: self.set_status(_('Prêt · en jeu, ouvre ton sac ou un coffre puis appuie sur F12.'))
                    self.draw_preview()
            elif kind == 'error':
                generation, message = value
                if generation == self.generation:
                    self.analysis = None
                    self.analyzing = False
                    self.refresh_results()
                    self.set_status(_('Capture illisible · reprends une capture avec F12.'), ERROR)
                    self.caption.configure(text=message[:110])
            elif kind == 'result':
                generation, path, result, before, cleanup_folder = value
                if generation != self.generation: continue
                self.analysis, self.current_path = result, path
                self.analyzing = False
                self.current_image = result.image
                self.set_status(*self.result_status(result))
                self.update_caption('')
                self.refresh_results()
                found = {}
                for detection in result.detections: found.setdefault(detection.item['id'], set()).add(detection.location)
                self.collections_view.set_found(found)
                self.announce(result)
                try:
                    write_json(BASE/'last-result.json', {'screenshot': str(path), 'analyzed_at': datetime.now().isoformat(), 'elapsed_seconds':round(result.elapsed,3), 'inventory_box': result.inventory_box, 'zones':result.zones, 'zone_labels':result.zone_labels, 'message': result.message, 'detections':[{'id':d.item['id'],'name':d.item['name_fr'],'tier':d.item['tier'],'location':d.location,'location_label':result.zone_labels.get(d.location,d.location),'box':d.box,'icon_box':d.icon_box,'cells':d.cells,'confidence':round(d.confidence,4),'collections':d.item['collections']} for d in result.detections]})
                except OSError:
                    logging.exception('Could not save the result')
                    self.update_caption(_(' · sauvegarde impossible, capture conservée'))
                    continue
                if cleanup_folder and self.delete_after.get() and self.watcher and self.watcher.folder == cleanup_folder:
                    self.recycle_result(path, before, cleanup_folder)
        self.after(100, self.pump)

    def announce(self, result):
        """Badge the Detection tab and flash the taskbar when a result arrives out of sight."""
        if self.tabs.select() != str(self.detection_tab):
            self.unseen = len({(d.item['id'], d.location) for d in result.detections})
            self.tabs.tab(self.detection_tab, text=_('Détection')+(f'  ● {self.unseen}' if self.unseen else '  ●'))
            self.tab_bar.set_badge(self.detection_tab, str(self.unseen) if self.unseen else '')
        try:
            if self.focus_displayof() is None:
                hwnd = ctypes.windll.user32.GetAncestor(self.winfo_id(), 2)
                ctypes.windll.user32.FlashWindow(hwnd, True)
        except (AttributeError, OSError, KeyError, tk.TclError):
            pass

    def recycle_result(self, path, before, folder):
        try:
            recycle_capture(path, before, folder)
        except Exception as error:
            logging.exception('Could not recycle %s', path)
            self.update_caption(_(' · suppression impossible, capture conservée'))
            return
        self.watcher.mark_processed(before)
        self.settings.setdefault('recycled_captures', {})[str(folder)] = list(self.watcher.emitted)
        self.save_settings()
        self.update_caption(_(' · capture dans la Corbeille'))

    # ------------------------------------------------------------------ sets in progress
    def small_icon(self, key, dim=False):
        if (key, dim) not in self.small_icons:
            self.small_icons[key, dim] = ImageTk.PhotoImage(load_icon(DATA, key, SET_ICON, dim), master=self)
        return self.small_icons[key, dim]

    def grouped_detections(self, only_visible=True):
        grouped = {}
        if self.analysis is None: return grouped
        for d in self.analysis.detections:
            if only_visible and not (self.filters[d.item['tier']].get() and self.locations[d.location].get()): continue
            grouped.setdefault((d.item['id'], d.location), []).append(d)
        return grouped

    def update_chips(self):
        everything = self.grouped_detections(only_visible=False)
        for key, chip in self.tier_chips.items():
            chip.set_count(sum(1 for d in everything.values() if d[0].item['tier'] == key) if self.analysis else None)
        for key, chip in self.location_chips.items():
            if key == 'trunk' and self.analysis is not None:
                chip.base = zone_text(self.analysis.zone_labels.get('trunk'))
            present = self.analysis is not None and key in self.analysis.zones
            chip.set_count(sum(1 for (_, place) in everything if place == key) if present else None)
        self.zoom_chip.refresh()

    def found_items(self):
        """item id -> visible detections of it on the current capture."""
        found = {}
        for (key, _), detections in self.grouped_detections().items(): found.setdefault(key, []).extend(detections)
        return found

    def collection_sets(self):
        """Every recipe with at least one visible item, most complete first, then rarest."""
        found = self.found_items()
        sets = []
        for recipe in self.collections_view.recipes:
            have = [entry['id'] for entry in recipe['items'] if entry['id'] in found]
            if have: sets.append((recipe, have))
        sets.sort(key=lambda entry: (-len(entry[1])/len(entry[0]['items']), -TIER_ORDER.index(entry[0]['tier']),
                                     entry[0]['id']))
        return sets, found

    def refresh_results(self):
        self.hover_key = None
        self.update_chips()
        if self.analysis is None:
            self.count_label.configure(text=_('SETS EN COURS'))
            self.sets_hint.configure(text='')
        else:
            sets, found = self.collection_sets()
            complete = sum(1 for recipe, have in sets if len(have) == len(recipe['items']))
            self.count_label.configure(text=f"{_('SETS EN COURS')}  ·  {len(sets)}")
            hint = plural(len(found), '{n} objet repéré', '{n} objets repérés')
            if complete: hint += '  ·  '+plural(complete, '{n} set complet', '{n} sets complets')
            self.sets_hint.configure(text=hint if sets else '')
        self.draw_sets()
        self.cards_canvas.yview_moveto(0)
        self.draw_preview()

    def sets_resized(self, event):
        if event.width != self.sets_width:
            self.sets_width = event.width
            self.draw_sets()

    def sets_message(self, text):
        width = max(120, self.cards_canvas.winfo_width()-16)
        self.cards_canvas.create_text(8, 18, text=text, fill=MUTED, font=('Segoe UI', 9), anchor='nw', width=width,
                                      tags='message')

    def draw_sets(self):
        canvas = self.cards_canvas
        canvas.delete('all')
        self.set_slots, self.set_order = {}, []
        width = max(220, canvas.winfo_width()-6)
        if self.analysis is None:
            self.sets_message(_('Analyse en cours…') if self.analyzing else
                              _('Les collections touchées par ta capture apparaîtront ici, avec les objets déjà '
                              'trouvés et ceux qui manquent.'))
        else:
            sets, found = self.collection_sets()
            if not sets:
                if self.analysis.detections:
                    self.sets_message(_('Tous les objets sont masqués par les filtres.\nClique sur une pastille grisée '
                                      'pour les réafficher.'))
                elif self.analysis.inventory_box:
                    self.sets_message(_('Rien à garder sur cette capture : aucun objet de collection dans les inventaires '
                                      'visibles.'))
                else:
                    self.sets_message(_('Aucun inventaire repéré.\n\nEn jeu, ouvre ton sac ou un coffre, puis refais F12. '
                                        'Pour une voiture, le premier onglet doit être actif.\n\nSi l’inventaire est bien '
                                        'ouvert, règle sa zone par un clic droit sur la capture.'))
            y = 0
            for recipe, have in sets:
                y = self.draw_set(recipe, have, found, y, width)+SET_GAP
        height = max(canvas.bbox('all')[3]+4 if canvas.find_all() else 0, 10)
        canvas.configure(scrollregion=(0, 0, width, height))
        fit_scrollbar(canvas, self.sets_scrollbar, height)

    def draw_set(self, recipe, have, found, y, width):
        canvas = self.cards_canvas
        color = COLORS[recipe['tier']]
        count = len(recipe['items'])
        complete = len(have) == count
        bottom = y+SET_H
        rid = recipe['id']
        shapes = self.collections_view.shapes
        shapes.rect(canvas, 0, y, width, bottom, 11, CARD, ACCENT if complete else None, 1.5, tags=('set', f'set-{rid}'))
        shapes.rect(canvas, 0, y+12, 3, bottom-12, 1.5, color)
        # Left: number chip and progress.
        number = f'{recipe_label(rid).split()[-1]}'
        shapes.rect(canvas, 10, y+9, 40, y+29, 7, mix(color, CARD, .18), tags=('set-head', f'head-{rid}'))
        canvas.create_text(25, y+19, text=number, fill=color, font=('Segoe UI', 10, 'bold'), tags=('set-head', f'head-{rid}'))
        canvas.create_text(25, y+40, text=f'{len(have)}/{count}', fill=ACCENT if complete else MUTED,
                           font=('Segoe UI', 9, 'bold'), tags=('set-head', f'head-{rid}', 'set-progress'))
        # Right: name, POI, state.
        slots_left = 48
        slot = SET_ICON+8
        text_x = slots_left+count*(slot+4)+4
        kind = _('Base') if rid < 7 else _('Suppl.') if rid < 14 else _('Spéciale') if rid < 24 else _('Unique')
        if text_x+50 < width:
            canvas.create_text(text_x, y+14, text=kind, fill=TEXT, font=('Segoe UI', 9, 'bold'), anchor='w',
                               tags=('set-head', f'head-{rid}'))
            detail = _('Complet') if complete else _('il manque {n}', n=count-len(have))
            canvas.create_text(text_x, y+29, text=detail, fill=ACCENT if complete else MUTED, font=('Segoe UI', 8),
                               anchor='w', tags=('set-head', f'head-{rid}'))
            if rid < 14:
                poi = POIS[rid % 7][0]
                canvas.create_image(text_x+7, y+44, image=self.collections_view.poi_image(poi, 14),
                                    tags=('set-head', f'head-{rid}'))
        # Progress bar along the bottom of the slots.
        bar_x2 = slots_left+count*(slot+4)-4
        shapes.rect(canvas, slots_left+2, bottom-7.5, bar_x2, bottom-4.5, 1.5, LINE)
        shapes.rect(canvas, slots_left+2, bottom-7.5, max(slots_left+5, slots_left+2+(bar_x2-slots_left-2)*len(have)/count),
                    bottom-4.5, 1.5, ACCENT if complete else color)
        for index, entry in enumerate(recipe['items']):
            key = entry['id']
            got = key in found
            x1 = slots_left+index*(slot+4)
            tag = f'slot-{rid}-{key}'
            fill = SLOT if got else mix(SLOT, CARD, .5)
            normal = (fill, ACCENT, 1.2) if got else (fill, None, 0)
            box = shapes.rect(canvas, x1, y+7, x1+slot, y+7+slot, 8, *normal, tags=('slot', tag))
            looks = shapes.variants(x1, y+7, x1+slot, y+7+slot, 8, normal, (fill, '#ffffff', 2))
            canvas.create_image(x1+slot/2, y+7+slot/2, image=self.small_icon(key, dim=not got), tags=('slot', tag))
            if entry['quantity'] > 1:
                pill = canvas.create_text(x1+slot-4, y+slot+2, text=f'×{entry["quantity"]}', anchor='e',
                                          fill=color if got else FAINT, font=('Segoe UI', 7, 'bold'), tags=('slot', tag))
                bx1, by1, bx2, by2 = canvas.bbox(pill)
                canvas.tag_lower(shapes.rect(canvas, bx1-3, by1-1, bx2+2, by2+1, 4, '#0d1015', tags=('slot', tag)), pill)
                canvas.tag_raise(pill)
            self.set_slots.setdefault(key, []).append((box, looks, got))
            canvas.tag_bind(tag, '<Enter>', lambda event, k=key, q=entry['quantity']: self.slot_enter(k, q, event))
            canvas.tag_bind(tag, '<Motion>', lambda event: self.tooltip.move(event.x_root, event.y_root))
            canvas.tag_bind(tag, '<Leave>', lambda event: self.after(80, self.slot_check_leave))
            canvas.tag_bind(tag, '<Button-1>', lambda event, k=key: self.collection_details(self.collections_view.items[k]))
        canvas.tag_bind(f'head-{rid}', '<Button-1>', lambda event, r=recipe: self.show_recipe(r))
        canvas.tag_bind(f'head-{rid}', '<Enter>', lambda event, r=recipe, h=have: self.tooltip.show(
            event.x_root, event.y_root, f'{collection_title(r)} · {_(TIER_NAMES[r["tier"]])}\n'
            +_('{have}/{total} objets sur cette capture', have=len(h), total=len(r['items']))+'\n'
            +_('Clic : ouvrir dans Collections')))
        canvas.tag_bind(f'head-{rid}', '<Leave>', lambda event: self.tooltip.hide())
        self.set_order.append(rid)
        return bottom

    def slot_enter(self, key, quantity, event):
        self.slot_hover(key, True)
        found = self.found_items().get(key, [])
        blocks = self.collections_view.origin_blocks(key, quantity)
        if found:
            places = ', '.join(sorted({_('ton sac') if d.location == 'bag' else
                                       zone_text(self.analysis.zone_labels.get('trunk')).lower() for d in found}))
            count = plural(len(found), '{n} emplacement', '{n} emplacements')
            blocks = [b for b in blocks if b[0] != 'found']  # the catalogue's « found on your last capture » line
            blocks.insert(1, ('found', _('✓ Sur cette capture : {places} ({count})', places=places, count=count)))
        else:
            blocks.insert(1, ('luck', _('○ Pas sur cette capture : il te manque cet objet')))
        self.tooltip.show_rich(event.x_root, event.y_root, blocks)

    def slot_check_leave(self):
        key = self.hover_key
        if key and any(tag.startswith('slot-') and tag.endswith('-'+key)
                       for item in items_under_pointer(self.cards_canvas) for tag in self.cards_canvas.gettags(item)):
            return
        self.slot_leave()

    def preview_check_leave(self):
        try:
            if self.winfo_containing(*self.winfo_pointerxy()) is self.canvas: return
        except (tk.TclError, KeyError):
            pass
        self.preview_hover(None)

    def slot_leave(self):
        self.tooltip.hide()
        if self.hover_key: self.slot_hover(self.hover_key, False)

    def slot_hover(self, key, active):
        if self.hover_key and self.hover_key != key: self.paint_slots(self.hover_key, False)
        self.hover_key = key if active else None
        self.paint_slots(key, active)
        self.highlight_cells(key if active else None)

    def paint_slots(self, key, active):
        for box, looks, got in self.set_slots.get(key, ()):
            try:
                self.cards_canvas.itemconfigure(box, image=looks[1 if active else 0])
            except tk.TclError:
                pass

    def show_recipe(self, recipe):
        self.tooltip.hide()
        self.tabs.select(self.collections_view)
        self.collections_view.show_recipe(recipe)

    # ------------------------------------------------------------------ preview
    def draw_onboarding(self, width, height):
        if self.analyzing:
            self.canvas.create_text(width/2, height/2, text=_('Analyse de la capture…'), fill=ACCENT,
                                    font=('Segoe UI', 15, 'bold'))
            return
        steps = []
        if self.watcher is None:
            steps.append((WARN, '1', _('Dans ROB RUN, fais une première capture avec F12')))
            steps.append((TEXT, '2', _('Clique sur « J’ai fait une capture » ci-dessous')))
            steps.append((TEXT, '3', _('Ensuite, ouvre ton sac ou un coffre et appuie sur F12')))
        else:
            steps.append((ACCENT, '✓', _('Dossier des captures trouvé')))
            steps.append((TEXT, '2', _('En jeu, ouvre ton sac, un coffre ou le coffre de la voiture')))
            steps.append((TEXT, '3', _('Appuie sur F12 : la capture est analysée automatiquement')))
        top = height/2-110
        self.canvas.create_text(width/2, top, text=_('Repère tes objets de collection'), fill=TEXT,
                                font=('Segoe UI', 16, 'bold'))
        left = max(30, width/2-220)
        for index, (color, mark, text) in enumerate(steps):
            y = top+60+index*48
            self.collections_view.shapes.rect(self.canvas, left, y-15, left+30, y+15, 15, None, color, 2)
            self.canvas.create_text(left+15, y, text=mark, fill=color, font=('Segoe UI', 11, 'bold'))
            self.canvas.create_text(left+46, y, text=text, fill=color if color != TEXT else TEXT, anchor='w',
                                    font=('Segoe UI', 11), width=width-left-70)
        if self.watcher is None:
            button = self.button(self.canvas, _('J’ai fait une capture'), self.first_capture_done, accent=True)
            self.canvas.create_window(width/2, top+60+3*48+20, window=button)
        else:
            self.canvas.create_text(width/2, top+60+3*48+16,
                                    text=_('Vert = Basique   ·   Violet = Purple   ·   Or = Gold'), fill=MUTED,
                                    font=('Segoe UI', 10))

    def draw_preview(self):
        self.preview_after = None
        if not hasattr(self, 'canvas'): return
        self.canvas.delete('all')
        self.canvas_targets = []
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 20 or height < 20: return
        if self.analysis is None:
            self.draw_onboarding(width, height)
            return
        image = self.analysis.image
        crop = (0, 0, image.width, image.height)
        shown_boxes = [box for key, box in self.analysis.zones.items() if self.locations[key].get()]
        if self.zoom.get() and shown_boxes:
            x1, y1, x2, y2 = (min(b[0] for b in shown_boxes), min(b[1] for b in shown_boxes),
                              max(b[2] for b in shown_boxes), max(b[3] for b in shown_boxes))
            crop = (max(0, x1-8), max(0, y1-6), min(image.width, x2+8), min(image.height, y2+8))
        preview = image.crop(crop)
        scale = min((width-24)/preview.width, (height-24)/preview.height)
        preview = preview.resize((max(1, round(preview.width*scale)), max(1, round(preview.height*scale))), Image.Resampling.LANCZOS)
        scale_x = preview.width/(crop[2]-crop[0])
        scale_y = preview.height/(crop[3]-crop[1])
        def to_preview(box):
            x1, y1, x2, y2 = box
            return ((x1-crop[0])*scale_x, (y1-crop[1])*scale_y, (x2-crop[0])*scale_x, (y2-crop[1])*scale_y)
        shown = [d for d in self.analysis.detections
                 if self.filters[d.item['tier']].get() and self.locations[d.location].get()]
        # Dim every inventory cell that is not worth keeping; kept cells stay at full brightness.
        mask = Image.new('L', preview.size, 0)
        painter = ImageDraw.Draw(mask)
        for key, zone in self.analysis.zones.items():
            if self.locations[key].get(): painter.rectangle(to_preview(zone), fill=255)
        for d in shown: painter.rectangle(to_preview(d.box), fill=0)
        self.preview_plain = preview
        preview = Image.composite(ImageEnhance.Brightness(preview).enhance(DIM), preview, mask)
        self.preview_image = preview
        self.photo = ImageTk.PhotoImage(preview)
        ox, oy = (width-preview.width)/2, (height-preview.height)/2
        self.preview_origin = (ox, oy)
        self.canvas.create_image(ox, oy, image=self.photo, anchor='nw')
        for d in shown:
            x1, y1, x2, y2 = to_preview(d.box)
            box = (ox+x1, oy+y1, ox+x2, oy+y2)
            self.canvas.create_rectangle(*box, outline=COLORS[d.item['tier']], width=2, tags='detected-cell')
            self.canvas_targets.append((box, d))
        if self.hover_key: self.highlight_cells(self.hover_key)

    def detection_at(self, x, y):
        for box, detection in self.canvas_targets:
            if box[0] <= x <= box[2] and box[1] <= y <= box[3]: return detection
        return None

    def preview_motion(self, event):
        detection = self.detection_at(event.x, event.y)
        if detection is None:
            self.preview_hover(None)
            return
        item = detection.item
        if item['id'] != self.hover_key:
            self.preview_hover(item['id'])
        place = _('Sac') if detection.location == 'bag' else zone_text(self.analysis.zone_labels.get('trunk'), 'Coffre / stockage')
        recipes = ', '.join(recipe_label(cid) for cid in item['collections'])
        self.tooltip.show(event.x_root, event.y_root,
                          f'{display_name(item)}\n{_(TIER_NAMES[item["tier"]])} · {recipes}\n{place}  ·  '
                          +_('clic : voir ses recettes'))
        self.canvas.configure(cursor='hand2')

    def preview_hover(self, key):
        if key is None:
            self.tooltip.hide()
            self.canvas.configure(cursor='')
        if key == self.hover_key: return
        if self.hover_key: self.paint_slots(self.hover_key, False)
        self.hover_key = key
        if key:
            self.paint_slots(key, True)
            slots = self.set_slots.get(key)
            if slots: self.scroll_into_view(slots[0][0])
        self.highlight_cells(key)

    def scroll_into_view(self, item):
        canvas = self.cards_canvas
        box = canvas.bbox(item)
        region = canvas.cget('scrollregion').split()
        if not box or len(region) < 4: return
        total = max(1, float(region[3]))
        first, last = (value*total for value in canvas.yview())
        if box[1] < first or box[3] > last:
            canvas.yview_moveto(max(0, box[1]-40)/total)

    def preview_click(self, event):
        detection = self.detection_at(event.x, event.y)
        if detection is not None:
            self.tooltip.hide()
            self.collection_details(detection.item)

    def highlight_cells(self, key):
        self.canvas.delete('hover-cell')
        if not key: return
        for box, detection in self.canvas_targets:
            if detection.item['id'] == key:
                x1, y1, x2, y2 = box
                self.canvas.create_rectangle(x1-3, y1-3, x2+3, y2+3, outline='#ffffff', width=3, tags='hover-cell')

    # ------------------------------------------------------------------ other actions
    def collection_details(self, item):
        self.tabs.select(self.collections_view)
        self.collections_view.show_item(item)

    def choose_zone(self, location='bag'):
        if not self.analysis:
            messagebox.showinfo(_('Zone de l’inventaire'), _('Analyse d’abord une capture avec cet inventaire ouvert.'))
            return
        dialog = tk.Toplevel(self)
        zone = _('Sac') if location == 'bag' else _('Coffre')
        dialog.title(_('Sélectionne les six colonnes : {zone}', zone=zone))
        dialog.configure(bg=BG)
        self.label(dialog, _('Trace un rectangle autour de la grille ({zone}), six colonnes jusqu’à la dernière ligne. Échap pour annuler.', zone=zone.upper()), padx=15, pady=12).pack()
        original = self.analysis.image
        ratio = min(1100/original.width, 700/original.height, self.winfo_screenwidth()*.85/original.width, self.winfo_screenheight()*.75/original.height)
        preview = original.resize((round(original.width*ratio), round(original.height*ratio)), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(preview)
        canvas = tk.Canvas(dialog, width=preview.width, height=preview.height, highlightthickness=0, cursor='crosshair')
        canvas.pack(padx=15, pady=(0, 15))
        canvas.create_image(0, 0, image=photo, anchor='nw')
        canvas.image = photo
        selection = {}
        def start(event):
            selection['start'] = (event.x, event.y)
            canvas.delete('selection')
        def drag(event):
            if 'start' not in selection: return
            canvas.delete('selection')
            canvas.create_rectangle(*selection['start'], event.x, event.y, outline=ACCENT, width=3, tags='selection')
        def finish(event):
            if 'start' not in selection: return
            a, b = selection['start']; c, d = event.x, event.y
            x1, x2 = sorted((max(0, min(a, preview.width)), max(0, min(c, preview.width))))
            y1, y2 = sorted((max(0, min(b, preview.height)), max(0, min(d, preview.height))))
            if x2-x1 < 80 or y2-y1 < 80: return
            normalized = [x1/preview.width, y1/preview.height, x2/preview.width, y2/preview.height]
            if location == 'bag': self.manual_zone = normalized
            else: self.manual_trunk = normalized
            self.save_settings(); dialog.destroy(); self.submit(self.current_path)
        canvas.bind('<ButtonPress-1>', start)
        canvas.bind('<B1-Motion>', drag)
        canvas.bind('<ButtonRelease-1>', finish)
        dialog.bind('<Escape>', lambda e: dialog.destroy())
        dialog.transient(self)
        dialog.grab_set()

    def reset_zone(self):
        self.manual_zone = None
        self.manual_trunk = None
        self.save_settings()
        if self.current_path: self.submit(self.current_path)

    def help(self):
        messagebox.showinfo(_('{app} — Aide', app=APP_NAME), _(HELP))

    def close(self):
        self.closed.set()
        try:
            self.settings['maximized'] = self.state() == 'zoomed'
            if self.state() == 'normal': self.settings['geometry'] = self.geometry()
        except tk.TclError:
            pass
        self.save_settings()
        self.tooltip.hide()
        self.destroy()


def main():
    parser = argparse.ArgumentParser(description=f'{APP_NAME} : détecteur local des collections ROB RUN')
    parser.add_argument('--folder', type=Path)
    parser.add_argument('--analyze', type=Path, help='Analyse sans ouvrir la fenêtre, résultat JSON sur stdout')
    args = parser.parse_args()
    logging.basicConfig(filename=str(BASE/'application.log'), level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
    if args.analyze:
        result = Detector(DATA).analyze(args.analyze)
        print(json.dumps({'message':result.message,'elapsed':result.elapsed,'zones':result.zones,'zone_labels':result.zone_labels,'inventory_box':result.inventory_box,'items':[{'id':d.item['id'],'name':d.item['name_fr'],'tier':d.item['tier'],'location':d.location,'score':d.confidence,'box':d.box,'icon_box':d.icon_box,'cells':d.cells} for d in result.detections]},ensure_ascii=False))
        return
    try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError): pass
    App(args.folder).mainloop()


if __name__ == '__main__':
    main()
