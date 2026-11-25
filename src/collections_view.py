"""Browsable recipe directory, laid out like the community collection posters."""
import json
import tkinter as tk
from icons import icon
import i18n
from i18n import _, plural
import unicodedata
import webbrowser
from tkinter import font as tkfont
from itertools import zip_longest
from tkinter import ttk
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageTk
from detector import COLORS, LABELS

BG, PANEL, MUTED, TEXT = '#101318', '#191e27', '#97a4b9', '#eef3fa'
ACCENT, FAINT, HIGHLIGHT, HOVER = '#b5ef83', '#5d6877', '#263142', '#2a3444'
CARD, SLOT, SLOT_HOVER, LINE, CHIP = '#161b23', '#1e252f', '#2a3341', '#252d39', '#1c222c'
TIER_NAMES = {'basic': 'Basique', 'purple': 'Purple', 'gold': 'Gold'}
# Crate value of each recipe (robrunstash.vercel.app/collections, before the Notoriety bonus).
SECTIONS = [
    ('basic', 'BASIQUE', '50 000 $ / collection', ['BASE  ·  01 – 07', 'SUPPLÉMENTAIRES  ·  08 – 14']),
    ('purple', 'PURPLE', '150 000 $ / collection', ['SPÉCIALES  ·  01 – 10']),
    ('gold', 'GOLD', '500 000 $ / collection', ['UNIQUES  ·  01 – 10']),
]
BADGE_W, TILE_PAD, POI_W, GAP, ROW_GAP, BLOCK_GAP, HEAD_H = 38, 8, 50, 8, 8, 26, 56
POI_MARGIN = 11  # space around the loot-location icon inside its tile
CELL_MAX, CELL_MIN = 96, 40
PAGES = [('basic',), ('purple', 'gold')]  # with « Toutes » and no search: Basique, then Purple + Gold
PAGER_H = 34
# Each basic slot (0-6, and its supplementary twin 7-13) belongs to one kind of map POI.
POIS = [('Cafe', 'Bar · café'), ('GasStation', 'Station-service'), ('AmmunitionShop', 'Équipement'),
        ('GunShop', 'Armurerie'), ('Jewelery', 'Bijouterie'), ('Pharmacy', 'Pharmacie'), ('Electronics', 'Électronique')]
SECTION_ICONS = {'basic': 'Icon_CollectionBase', 'purple': 'Icon_CollectionSpecial', 'gold': 'Icon_CollectionUnique'}
ITEM_PAGE = 'https://robrunstash.vercel.app/{}items/{}/'
ORIGIN_WORDS = {}  # French origin fragment -> {language: text}; filled from data/origins-i18n.json


def item_page(key):
    lang = i18n.current()
    return ITEM_PAGE.format('' if lang not in ('fr', 'de', 'es', 'pt', 'ru') else lang+'/', key)


def origin_word(text):
    """A place, seller or container name from origins.json (French) in the interface language."""
    lang = i18n.current()
    if lang == 'fr': return text
    words = ORIGIN_WORDS.get(text.replace("'", '’')) or ORIGIN_WORDS.get(text) or {}
    return words.get(lang) or words.get('en') or text


def origin_place(text):
    return ', '.join(origin_word(part) for part in text.split(', '))


def display_name(item):
    return i18n.item_name(item).replace('\\u00A0', ' ').replace('\u00a0', ' ')


def recipe_number(cid):
    """Poster numbering: green 01-14, purple 01-10, gold 01-10."""
    return cid+1 if cid < 14 else cid-13 if cid < 24 else cid-23


def recipe_label(cid):
    """Human name of a recipe slot from its catalogue id (0-33)."""
    kind = _('Base') if cid < 7 else _('Supplémentaire') if cid < 14 else _('Spéciale') if cid < 24 else _('Unique')
    return f'{kind} {recipe_number(cid):02d}'


def collection_title(recipe):
    return recipe_label(recipe['id'])


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if not unicodedata.combining(c))


def describe_origin(lines, limit=6):
    """Turn origins.json lines into (guaranteed, heading, places) tuples for the tooltip."""
    result = []
    for line in lines:
        odds, title, places = (line.split('|', 2)+['', ''])[:3]
        names = [p for p in places.split(';') if p]
        def fmt(place):
            name, _x, count = place.partition('~')
            return f'{origin_place(name)} ({count})' if count else origin_place(name)
        if title.startswith('Acheter : '):
            seller, _x, price = title[len('Acheter : '):].rpartition(', ')
            heading = _('Acheter {price} — {seller}', price=price, seller=origin_word(seller))
            body = ', '.join(origin_place(p.partition('~')[0]) for p in names)
        elif title.startswith('Fouiller : '):
            heading = _('Fouiller « {what} »', what=origin_word(title[len('Fouiller : '):]))
            body = ', '.join(fmt(p) for p in names)
        elif title == 'Objets qui traînent':
            heading = _('Objets qui traînent (emplacements au hasard)')
            body = ', '.join(fmt(p) for p in names[:limit])
            if len(names) > limit: body += ' '+_('+ {n} autres lieux', n=len(names)-limit)
        elif title == 'Conteneurs (braquages)':
            heading, body = _('Conteneurs fouillés pendant les braquages'), _('armoires, bureaux, coffres-forts…')
        else:
            heading, body = origin_word(title), ', '.join(origin_word(p) for p in names) if names else places
        result.append((odds == 'G', heading, body))
    return result


def mix(color, other, amount):
    """Blend two #rrggbb colours: amount=1 gives color, 0 gives other."""
    a = [int(color[i:i+2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i+2], 16) for i in (1, 3, 5)]
    return '#'+''.join(f'{round(x*amount+y*(1-amount)):02x}' for x, y in zip(a, b))


def load_icon(data, key, size, dim=False):
    """Item icon fitted in a square; dim=True gives the faded look of a missing item."""
    with Image.open(data/'icons'/f'{key}.png') as source:
        icon = source.convert('RGBA')
    icon.thumbnail((size, size), Image.Resampling.LANCZOS)
    if dim:
        grey = icon.convert('LA').convert('RGBA')
        alpha = icon.getchannel('A').point(lambda value: value*.32)
        grey.putalpha(alpha)
        icon = grey
    return icon


def items_under_pointer(canvas):
    """Canvas items really under the mouse right now (ignores spurious Leave events)."""
    try:
        px, py = canvas.winfo_pointerxy()
        if canvas.winfo_containing(px, py) is not canvas: return ()
        x, y = canvas.canvasx(px-canvas.winfo_rootx()), canvas.canvasy(py-canvas.winfo_rooty())
        return canvas.find_overlapping(x, y, x, y)
    except (tk.TclError, KeyError):
        return ()


class Shapes:
    """Antialiased rounded shapes drawn as canvas images: Tk polygons have jagged edges on Windows."""
    SCALE = 4

    def __init__(self, master):
        self.master, self.cache = master, {}

    def image(self, width, height, radius, fill, outline=None, line=0):
        width, height = max(1, int(width)), max(1, int(height))
        key = (width, height, radius, fill, outline, line)
        if key not in self.cache:
            s = self.SCALE
            size = (width*s, height*s)
            r = max(0., min(radius, width/2, height/2))*s
            outer = Image.new('L', size, 0)
            ImageDraw.Draw(outer).rounded_rectangle((0, 0, size[0]-1, size[1]-1), r, fill=255)
            if outline and line:
                inset = line*s
                inner = Image.new('L', size, 0)
                ImageDraw.Draw(inner).rounded_rectangle((inset, inset, size[0]-1-inset, size[1]-1-inset),
                                                        max(0., r-inset), fill=255)
                shape = Image.new('RGBA', size, outline)
                if fill:
                    shape.paste(Image.new('RGBA', size, fill), (0, 0), inner)
                    shape.putalpha(outer)
                else:
                    shape.putalpha(ImageChops.subtract(outer, inner))
            else:
                shape = Image.new('RGBA', size, ImageColor.getrgb(fill)+(255,))
                shape.putalpha(outer)
            self.cache[key] = ImageTk.PhotoImage(shape.reduce(s), master=self.master)
        return self.cache[key]

    @staticmethod
    def box(x1, y1, x2, y2):
        left, top = int(round(x1)), int(round(y1))
        return left, top, int(round(x2))-left, int(round(y2))-top

    def rect(self, canvas, x1, y1, x2, y2, radius, fill, outline=None, line=0, tags=()):
        left, top, width, height = self.box(x1, y1, x2, y2)
        return canvas.create_image(left, top, image=self.image(width, height, radius, fill, outline, line),
                                   anchor='nw', tags=tags)

    def variants(self, x1, y1, x2, y2, radius, *styles):
        """Same-size images for several (fill, outline, line) styles, e.g. normal and hover."""
        _x, _x, width, height = self.box(x1, y1, x2, y2)
        return [self.image(width, height, radius, *style) for style in styles]


def fit_scrollbar(canvas, scrollbar, content_height):
    """Show the vertical scrollbar only when the content is taller than the visible area."""
    needed = content_height > canvas.winfo_height()+1
    shown = scrollbar.winfo_manager() == 'pack'
    if needed and not shown:
        scrollbar.pack(side='right', fill='y', before=canvas)
    elif not needed and shown:
        scrollbar.pack_forget()
        canvas.yview_moveto(0)


def round_rect(canvas, x1, y1, x2, y2, r=10, **options):
    points = [x1+r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y2-r, x2, y2, x2-r, y2, x1+r, y2, x1, y2, x1, y2-r, x1, y1+r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **options)


class Tooltip:
    """Small dark bubble that follows the pointer; plain text or styled blocks."""
    STYLES = {
        'title': dict(fg=TEXT, font=('Segoe UI', 10, 'bold')),
        'recipe': dict(fg=MUTED, font=('Segoe UI', 8)),
        'found': dict(fg=ACCENT, font=('Segoe UI', 9, 'bold')),
        'section': dict(fg=MUTED, font=('Segoe UI', 8, 'bold'), pady=(8, 2)),
        'sure': dict(fg=ACCENT, font=('Segoe UI', 9, 'bold'), pady=(4, 0)),
        'luck': dict(fg='#f0b25a', font=('Segoe UI', 9, 'bold'), pady=(4, 0)),
        'body': dict(fg=TEXT, font=('Segoe UI', 9), padx=(16, 0)),
        'foot': dict(fg=FAINT, font=('Segoe UI', 8), pady=(8, 0)),
        'plain': dict(fg=TEXT, font=('Segoe UI', 9)),
    }

    def __init__(self, root):
        self.root = root
        self.window = None
        self.frame = None
        self.content = None
        self.visible = False

    def _ensure(self):
        if self.window is None:
            self.window = tk.Toplevel(self.root)
            self.window.wm_overrideredirect(True)
            try: self.window.attributes('-topmost', True)
            except tk.TclError: pass
            self.window.configure(bg='#3a465a')
            self.frame = tk.Frame(self.window, bg='#252e3c', padx=12, pady=9)
            self.frame.pack(padx=1, pady=1)
            self.visible = True
            self.click_through()

    def click_through(self):
        """Windows: let the mouse pass through the bubble so it never steals Enter/Leave events."""
        try:
            import ctypes
            self.window.attributes('-alpha', .98)
            self.window.update_idletasks()
            hwnd = int(self.window.wm_frame(), 16)
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, style | 0x20 | 0x80000 | 0x08000000)
        except (AttributeError, OSError, ValueError, tk.TclError):
            pass

    def show(self, x, y, text):
        self.show_rich(x, y, [('plain', text)])

    def show_rich(self, x, y, blocks):
        self._ensure()
        if blocks != self.content:
            self.content = blocks
            for child in self.frame.winfo_children(): child.destroy()
            for style, text in blocks:
                options = dict(self.STYLES.get(style, self.STYLES['plain']))
                pady, padx = options.pop('pady', (0, 0)), options.pop('padx', (0, 0))
                tk.Label(self.frame, text=text, bg='#252e3c', justify='left', anchor='w', wraplength=420,
                         **options).pack(fill='x', anchor='w', pady=pady, padx=padx)
        self.move(x, y)
        if not self.visible:
            self.window.deiconify()
            self.window.lift()
            self.visible = True

    def move(self, x, y):
        if self.window is None: return
        if self.content != getattr(self, 'measured', None):
            self.window.update_idletasks()
            self.measured = self.content
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        left = x+16 if x+16+width < screen_w else max(0, x-16-width)
        top = y+14 if y+14+height < screen_h else max(0, y-14-height)
        self.window.wm_geometry(f'+{left}+{top}')

    def text(self):
        return '\n'.join(text for _, text in self.content or [])

    def hide(self):
        if self.window is not None and self.visible:
            self.window.withdraw()
            self.visible = False


class CollectionsView(tk.Frame):
    def __init__(self, parent, data):
        super().__init__(parent, bg=BG)
        self.data = data
        self.catalog = json.loads((data/'catalog.json').read_text(encoding='utf-8'))
        try:
            self.origins = json.loads((data/'origins.json').read_text(encoding='utf-8'))['items']
        except (OSError, ValueError, KeyError):
            self.origins = {}
        try: ORIGIN_WORDS.update(json.loads((data/'origins-i18n.json').read_text(encoding='utf-8')))
        except (OSError, ValueError): pass
        self.items = {item['id']: item for item in self.catalog['items']}
        self.recipes = sorted(self.catalog['collections'], key=lambda recipe: recipe['id'])
        self.photos = {}
        self.icon_size = None
        self.layout = None
        self.page = 0
        self.paged = False
        self.drawn_recipes = []
        self.poi_photos = {}
        self.poi_drawn = []
        self.query = tk.StringVar(master=self)
        self.tier = tk.StringVar(master=self, value='all')
        self.found_only = tk.BooleanVar(master=self, value=False)
        self.found = {}
        self.visible_recipes = []
        self.recipe_boxes = {}
        self.rendered_text = {}
        self.cells = {}
        self.pending = None
        self.hide_after = None
        self.hovered = None
        self.columns = 2
        self.width = 0
        self.ready = False
        self.dirty = False
        self.tooltip = Tooltip(self)
        self.shapes = Shapes(self)

        filters = tk.Frame(self, bg=BG)
        filters.pack(fill='x', pady=(12, 10))
        box = tk.Frame(filters, bg=PANEL)
        box.pack(side='left', fill='x', expand=True, padx=(0, 12))
        tk.Label(box, image=icon(self, 'search', MUTED, 15), bg=PANEL).pack(side='left', padx=(12, 8))
        self.search = tk.Entry(box, textvariable=self.query, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                               relief='flat', bd=0, highlightthickness=0, font=('Segoe UI', 11), width=20)
        self.search.pack(side='left', fill='x', expand=True, ipady=8)
        self.placeholder = tk.Label(box, text=_('Rechercher un objet ou une collection…'), bg=PANEL, fg=FAINT,
                                    font=('Segoe UI', 11), cursor='xterm')
        self.placeholder.place(in_=self.search, x=1, rely=.5, anchor='w')
        self.placeholder.bind('<Button-1>', lambda event: self.search.focus_set())
        self.clear_button = tk.Label(box, image=icon(self, 'close', MUTED, 12), bg=PANEL,
                                     padx=12, cursor='hand2')
        self.clear_button.bind('<Button-1>', lambda event: self.clear_search())
        self.search.bind('<Escape>', lambda event: self.clear_search())

        self.tier_buttons = {}
        for key, title in [('all', _('Toutes')), *((k, _(v)) for k, v in TIER_NAMES.items())]:
            color = TEXT if key == 'all' else COLORS[key]
            button = tk.Radiobutton(filters, text=title, variable=self.tier, value=key, indicatoron=False,
                                    bg=PANEL, fg=color, selectcolor='#344152', activebackground='#344152',
                                    activeforeground=color, relief='flat', offrelief='flat', bd=0,
                                    highlightthickness=0, padx=12, pady=8, font=('Segoe UI', 10),
                                    cursor='hand2', command=self.render)
            button.pack(side='left', padx=2)
            self.tier_buttons[key] = button
        self.found_button = tk.Checkbutton(filters, text=_('Repérées'), compound='left',
                                           image=icon(self, 'check', FAINT, 12, gap=2), variable=self.found_only, indicatoron=False,
                                           bg=PANEL, fg=ACCENT, selectcolor='#2f4a2a', activebackground='#344152',
                                           activeforeground=ACCENT, relief='flat', offrelief='flat', bd=0,
                                           highlightthickness=0, padx=12, pady=8, font=('Segoe UI', 10),
                                           cursor='hand2', command=self.render)
        self.found_button.pack(side='left', padx=(10, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        self.scrollbar = scrollbar = ttk.Scrollbar(body, orient='vertical', command=self.canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.bind('<Configure>', self.resized)
        self.canvas.bind('<Leave>', lambda event: self.schedule_leave())
        self.item_menu = tk.Menu(self, tearoff=False)
        self.query.trace_add('write', self.schedule_search)
        self.summary = ''
        self.update_search_box()

    def label(self, parent, text='', **options):
        style = dict(bg=parent.cget('bg'), fg=TEXT, font=('Segoe UI', 10), anchor='w', justify='left')
        style.update(options)
        return tk.Label(parent, text=text, **style)

    # ------------------------------------------------------------------ state
    def show(self):
        if not self.ready or self.dirty: self.render(reset_scroll=not self.ready)

    def focus_search(self):
        self.search.focus_set()
        self.search.select_range(0, 'end')

    def clear_search(self):
        self.query.set('')
        self.render()
        self.search.focus_set()

    def update_search_box(self):
        if self.query.get():
            self.placeholder.place_forget()
            if not self.clear_button.winfo_ismapped(): self.clear_button.pack(side='right')
        else:
            self.placeholder.place(in_=self.search, x=1, rely=.5, anchor='w')
            self.clear_button.pack_forget()

    def schedule_search(self, *args):
        self.update_search_box()
        if self.pending: self.after_cancel(self.pending)
        self.pending = self.after(160, self.render)

    def resized(self, event):
        if (event.width, event.height) == (self.width, getattr(self, 'height', 0)): return
        self.width, self.height = event.width, event.height
        if self.ready:
            if self.pending: self.after_cancel(self.pending)
            self.pending = self.after(60, lambda: self.render(reset_scroll=False))

    def set_found(self, found):
        """found: item id -> set of locations ('bag', 'trunk') from the last capture."""
        self.found = {key: set(value) for key, value in found.items()}
        if not self.found: self.found_only.set(False)
        if self.ready:
            if self.winfo_ismapped(): self.render(reset_scroll=False)
            else: self.dirty = True

    def found_in(self, recipe):
        return sum(entry['id'] in self.found for entry in recipe['items'])

    def text_match(self, recipe, query):
        if not query: return True
        number = recipe_number(recipe['id'])
        words = [collection_title(recipe), f'{number}', LABELS[recipe['tier']], _(TIER_NAMES[recipe['tier']])]
        for entry in recipe['items']:
            item = self.items[entry['id']]
            words.extend((display_name(item), item.get('name', ''), *(item.get('names') or {}).values()))
        return query in normalize(' '.join(words))

    def matches(self, recipe):
        if self.tier.get() != 'all' and recipe['tier'] != self.tier.get(): return False
        if self.found_only.get() and not self.found_in(recipe): return False
        return self.text_match(recipe, normalize(self.query.get().strip()))

    def item_matches(self, item, query):
        return bool(query) and query in normalize(display_name(item)+' '+item.get('name', ''))

    def icon(self, key, size):
        if size != self.icon_size:
            self.photos, self.icon_size = {}, size
        if key not in self.photos:
            self.photos[key] = ImageTk.PhotoImage(load_icon(self.data, key, size), master=self)
        return self.photos[key]

    def poi_image(self, name, size, color=None):
        key = (name, size, color)
        if key not in self.poi_photos:
            with Image.open(self.data/'poi'/f'{name}.png') as source:
                icon = source.convert('RGBA')
            icon.thumbnail((size, size), Image.Resampling.LANCZOS)
            if color:
                tint = Image.new('RGBA', icon.size, color)
                rgb = ImageChops.multiply(icon, tint)
                rgb.putalpha(icon.getchannel('A'))
                icon = rgb
            self.poi_photos[key] = ImageTk.PhotoImage(icon, master=self)
        return self.poi_photos[key]

    def update_chips(self, searched):
        counts = {key: sum(recipe['tier'] == key for recipe in searched) for key in TIER_NAMES}
        counts['all'] = len(searched)
        for key, button in self.tier_buttons.items():
            title = _('Toutes') if key == 'all' else _(TIER_NAMES[key])
            button.configure(text=f'{title}  {counts[key]}')
        with_found = sum(1 for recipe in self.recipes if self.found_in(recipe))
        self.found_button.configure(text=f"{_('Repérées')}  {with_found}" if self.found else _('Repérées'),
                                    image=icon(self, 'check', ACCENT if self.found else FAINT, 12, gap=2),
                                    state='normal' if self.found else 'disabled', disabledforeground=FAINT)

    # ------------------------------------------------------------------ drawing
    def render(self, reset_scroll=True):
        if self.pending:
            self.after_cancel(self.pending)
            self.pending = None
        self.ready, self.dirty = True, False
        self.leave_item()
        self.update_search_box()
        query = normalize(self.query.get().strip())
        searched = [recipe for recipe in self.recipes if self.text_match(recipe, query)
                    and (not self.found_only.get() or self.found_in(recipe))]
        self.update_chips(searched)
        self.visible_recipes = [recipe for recipe in self.recipes if self.matches(recipe)]
        shown = len(self.visible_recipes)
        basic = [recipe for recipe in self.visible_recipes if recipe['tier'] == 'basic']
        others = [recipe for recipe in self.visible_recipes if recipe['tier'] != 'basic']
        self.paged = self.tier.get() == 'all' and not self.query.get().strip() and bool(basic) and bool(others)
        query_text = self.query.get().strip()
        self.summary = plural(shown, '{n} collection pour « {query} »', '{n} collections pour « {query} »',
                              query=query_text) if query_text else ''

        canvas = self.canvas
        canvas.delete('all')
        self.recipe_boxes, self.rendered_text, self.cells, self.poi_drawn = {}, {}, {}, []
        if canvas.winfo_width() < 50: canvas.update_idletasks()
        if canvas.winfo_width() >= 50: self.width = canvas.winfo_width()
        width = max(360, self.width or canvas.winfo_width())
        height = max(200, canvas.winfo_height())
        visible = {recipe['id'] for recipe in self.visible_recipes}
        blocks = [(tier, title, price, subtitles, [r for r in self.visible_recipes if r['tier'] == tier])
                  for tier, title, price, subtitles in SECTIONS]
        blocks = [block for block in blocks if block[4]]
        top = 2
        if self.summary and self.visible_recipes:
            canvas.create_text(2, 10, text=self.summary, anchor='w', fill=MUTED, font=('Segoe UI', 9), tags='summary')
            top = 26
        if self.paged:
            blocks = [block for block in blocks if block[0] in PAGES[self.page]]
            top = self.draw_pager(width, len(basic), len(others))+14
        self.drawn_recipes = [recipe for block in blocks for recipe in block[4]]
        mode, cell = self.choose_layout(blocks, visible, width, height-top)
        self.layout, self.columns = (mode, cell), 2
        sizes = [self.block_size(block, visible, cell, width if mode == 'stack' else None) for block in blocks]
        if mode == 'row':
            total = sum(w for w, _ in sizes)+BLOCK_GAP*(len(blocks)-1)
            x, bottom = max(0, (width-total)/2), 0
            for block, (w, h) in zip(blocks, sizes):
                bottom = max(bottom, self.draw_block(block, visible, cell, x, top, w))
                x += w+BLOCK_GAP
        elif mode == 'split':
            first = sizes[0][0]
            right = max(w for w, _ in sizes[1:])
            x = max(0, (width-first-BLOCK_GAP-right)/2)
            bottom = self.draw_block(blocks[0], visible, cell, x, top, first)
            y = top
            for block, (w, h) in zip(blocks[1:], sizes[1:]):
                y = self.draw_block(block, visible, cell, x+first+BLOCK_GAP, y, right)+BLOCK_GAP/2
            bottom = max(bottom, y)
        else:
            y = top
            for block, (w, h) in zip(blocks, sizes):
                y = self.draw_block(block, visible, cell, max(0, (width-w)/2), y, w)+BLOCK_GAP/2
            bottom = y
        if not self.visible_recipes:
            hint = _('Aucune collection ne correspond.')
            if self.query.get().strip(): hint += _('  Efface la recherche (Échap)')
            if self.tier.get() != 'all' or self.found_only.get(): hint += _(' ou choisis « Toutes ».')
            canvas.create_text(4, 30, text=hint, fill=MUTED, font=('Segoe UI', 10), anchor='w', tags='empty')
            bottom = 60
        canvas.configure(scrollregion=(0, 0, width, max(bottom+4, 10)))
        fit_scrollbar(canvas, self.scrollbar, bottom+4)
        if reset_scroll: canvas.yview_moveto(0)

    # ------------------------------------------------------------------ pages
    def draw_pager(self, width, basic_count, other_count):
        """Segmented control: page 1 Basique, page 2 Purple + Gold."""
        canvas, shapes = self.canvas, self.shapes
        font = ('Segoe UI', 10, 'bold')
        segments = [(_('Basique'), f'{basic_count}', [COLORS['basic']]),
                    (_('Purple & Gold'), f'{other_count}', [COLORS['purple'], COLORS['gold']])]
        measure = tkfont.Font(root=self, font=font).measure
        widths = [measure(label)+measure(count)+58 for label, count, _ in segments]
        total = sum(widths)+8
        x = (width-total)/2
        shapes.rect(canvas, x, 0, x+total, PAGER_H, PAGER_H/2, CHIP, tags='pager')
        left = x+4
        for index, ((label, count, colors), w) in enumerate(zip(segments, widths)):
            active = index == self.page
            tag = f'page-{index}'
            shapes.rect(canvas, left, 4, left+w, PAGER_H-4, (PAGER_H-8)/2, SLOT_HOVER if active else CHIP,
                        tags=('pager', tag))
            dot_x = left+16
            for offset, color in enumerate(colors):
                shapes.rect(canvas, dot_x+offset*7-4, PAGER_H/2-4, dot_x+offset*7+4, PAGER_H/2+4, 4, color,
                            tags=('pager', tag))
            text_x = dot_x+len(colors)*7+6
            name = canvas.create_text(text_x, PAGER_H/2, text=label, anchor='w', font=font,
                                      fill=TEXT if active else MUTED, tags=('pager', tag))
            canvas.create_text(canvas.bbox(name)[2]+7, PAGER_H/2, text=count, anchor='w', font=('Segoe UI', 9),
                               fill=MUTED if active else FAINT, tags=('pager', tag))
            canvas.tag_bind(tag, '<Button-1>', lambda event, i=index: self.set_page(i))
            canvas.tag_bind(tag, '<Enter>', lambda event: canvas.configure(cursor='hand2'))
            canvas.tag_bind(tag, '<Leave>', lambda event: canvas.configure(cursor=''))
            left += w
        for offset, name in ((14, 'left'), (28, 'right')):  # keyboard hint: ← → turn the pages
            canvas.create_image(x+total+offset, PAGER_H/2, image=icon(self, name, FAINT, 12), anchor='w', tags='pager')
        return PAGER_H

    def set_page(self, page):
        page = max(0, min(len(PAGES)-1, page))
        if page != self.page or not self.ready:
            self.page = page
            self.render()

    def turn_page(self, step):
        if self.paged: self.set_page(self.page+step)

    # ------------------------------------------------------------------ layout
    @staticmethod
    def tile_width(count, cell):
        return BADGE_W+count*cell+TILE_PAD

    @staticmethod
    def tile_height(cell):
        return cell+8

    def per_row(self, available, cell):
        if available is None: return 2
        return max(1, min(5, int((available+GAP)//(self.tile_width(3, cell)+GAP))))

    def block_size(self, block, visible, cell, available=None):
        tier, _x, _x, _x, recipes = block
        row = self.tile_height(cell)+ROW_GAP
        if tier == 'basic':
            rows = sum(1 for slot in range(7) if slot in visible or slot+7 in visible)
            return POI_W+2*self.tile_width(4, cell)+2*GAP, HEAD_H+rows*row
        per_row = self.per_row(available, cell)
        rows = -(-len(recipes)//per_row)
        return per_row*self.tile_width(3, cell)+(per_row-1)*GAP, HEAD_H+rows*row

    def choose_layout(self, blocks, visible, width, height):
        """Largest tiles that show everything without scrolling; otherwise the shortest layout."""
        if not blocks: return 'stack', CELL_MAX
        options = []
        for mode in ('row', 'split', 'stack'):
            if mode == 'split' and len(blocks) < 3: continue
            for cell in range(CELL_MAX, CELL_MIN-1, -2):
                sizes = [self.block_size(b, visible, cell, width if mode == 'stack' else None) for b in blocks]
                if mode == 'row':
                    need_w = sum(w for w, _ in sizes)+BLOCK_GAP*(len(blocks)-1)
                    need_h = max(h for _, h in sizes)
                elif mode == 'split':
                    need_w = sizes[0][0]+BLOCK_GAP+max(w for w, _ in sizes[1:])
                    need_h = max(sizes[0][1], sum(h for _, h in sizes[1:])+BLOCK_GAP/2*(len(sizes)-2))
                else:
                    need_w = max(w for w, _ in sizes)
                    need_h = sum(h for _, h in sizes)+BLOCK_GAP/2*(len(sizes)-1)
                if need_w > width: continue
                if need_h <= height: return mode, cell
                options.append((need_h, -cell, mode))
        if options:
            _x, cell, mode = min(options)
            return mode, -cell
        return 'stack', CELL_MIN

    def draw_block(self, block, visible, cell, x, y, width):
        tier, title, price, subtitles, recipes = block
        canvas, color = self.canvas, COLORS[tier]
        canvas.create_image(x+2, y+13, image=self.poi_image(SECTION_ICONS[tier], 20, color), anchor='w')
        heading = canvas.create_text(x+30, y+13, text=_(title), fill=color, font=('Segoe UI', 12, 'bold'), anchor='w')
        total = sum(1 for recipe in self.recipes if recipe['tier'] == tier)
        canvas.create_text(canvas.bbox(heading)[2]+8, y+14, text=f'{len(recipes)}/{total}' if len(recipes) != total else _('{n} collections', n=total),
                           fill=MUTED, font=('Segoe UI', 9), anchor='w')
        label = canvas.create_text(x+width-10, y+13, text=_(price), fill=TEXT, font=('Segoe UI', 9, 'bold'), anchor='e')
        x1, y1, x2, y2 = canvas.bbox(label)
        pill = self.shapes.rect(canvas, x1-10, y1-4, x2+10, y2+4, 9, mix(color, BG, .16), tags=f'price-{tier}')
        canvas.tag_lower(pill, label)
        canvas.addtag_withtag(f'price-{tier}', label)
        canvas.tag_bind(f'price-{tier}', '<Enter>', lambda event: self.tooltip.show(
            event.x_root, event.y_root, _('Valeur de la caisse de récompense, hors bonus de Notoriété (jusqu’à +25 %)')))
        canvas.tag_bind(f'price-{tier}', '<Leave>', lambda event: self.tooltip.hide())
        canvas.create_line(x, y+31, x+width, y+31, fill=mix(color, BG, .35), width=1)
        row = self.tile_height(cell)+ROW_GAP
        top = y+HEAD_H
        if tier == 'basic':
            tile = self.tile_width(4, cell)
            columns = [x+POI_W+GAP, x+POI_W+2*GAP+tile]
            for left, text in zip(columns, subtitles):
                canvas.create_text(left+6, y+43, text=_(text), fill=FAINT, font=('Segoe UI', 8, 'bold'), anchor='w')
            for slot, (poi, poi_name) in enumerate(POIS):
                if slot not in visible and slot+7 not in visible: continue
                height = self.tile_height(cell)
                self.shapes.rect(canvas, x, top, x+POI_W, top+height, 10, CHIP, tags=('poi', f'poi-{slot}'))
                canvas.create_image(x+POI_W/2, top+height/2, image=self.poi_image(poi, max(14, min(POI_W-2*POI_MARGIN, height-2*POI_MARGIN))),
                                    tags=('poi', f'poi-{slot}'))
                self.poi_drawn.append((slot, poi, poi_name))
                text = f'{_(poi_name)}\n'+_('Lieu des collections {a} et {b}', a=recipe_label(slot), b=recipe_label(slot+7))
                canvas.tag_bind(f'poi-{slot}', '<Enter>', lambda event, t=text: self.tooltip.show(event.x_root, event.y_root, t))
                canvas.tag_bind(f'poi-{slot}', '<Leave>', lambda event: self.tooltip.hide())
                for column, cid in zip(columns, (slot, slot+7)):
                    if cid in visible: self.draw_tile(self.recipes[cid], column, top, cell)
                top += row
        else:
            canvas.create_text(x+6, y+43, text=_(subtitles[0]), fill=FAINT, font=('Segoe UI', 8, 'bold'), anchor='w')
            per_row = max(1, int((width+GAP)//(self.tile_width(3, cell)+GAP)))
            for index, recipe in enumerate(recipes):
                left = x+(index % per_row)*(self.tile_width(3, cell)+GAP)
                self.draw_tile(recipe, left, top+(index//per_row)*row, cell)
            top += -(-len(recipes)//per_row)*row
        return top-ROW_GAP

    def draw_tile(self, recipe, x, y, cell):
        canvas = self.canvas
        color = COLORS[recipe['tier']]
        query = normalize(self.query.get().strip())
        count = len(recipe['items'])
        height = self.tile_height(cell)
        width = self.tile_width(count, cell)
        bottom, middle = y+height, y+height/2
        found = self.found_in(recipe)
        complete = found == count
        shapes = self.shapes
        shapes.rect(canvas, x, y, x+width, bottom, 11, CARD, ACCENT if complete else None, 1.5)
        shapes.rect(canvas, x, y+12, x+3, bottom-12, 1.5, color)
        number = f'{recipe_number(recipe["id"]):02d}'
        texts = [collection_title(recipe), number]
        chip_x1, chip_x2 = x+9, x+BADGE_W-3
        big = cell >= 72  # larger tiles get larger labels
        half = 13 if big else 11
        chip_y = middle-(6 if found else 0)
        shapes.rect(canvas, chip_x1, chip_y-half, chip_x2, chip_y+half, 7, mix(color, CARD, .18))
        canvas.create_text((chip_x1+chip_x2)/2, chip_y, text=number, fill=color, font=('Segoe UI', 12 if big else 10, 'bold'))
        if found:
            bar_y = chip_y+half+6
            shapes.rect(canvas, chip_x1+1, bar_y-1.5, chip_x2-1, bar_y+1.5, 1.5, LINE)
            shapes.rect(canvas, chip_x1+1, bar_y-1.5, max(chip_x1+4, chip_x1+1+(chip_x2-chip_x1-2)*found/count),
                        bar_y+1.5, 1.5, ACCENT)
            texts.append(_('{found}/{count} repérés', found=found, count=count))
        icon_size = cell-14
        for index, entry in enumerate(recipe['items']):
            key = entry['id']; item = self.items[key]
            tag = f'cell-{recipe["id"]}-{index}'
            x1 = x+BADGE_W+TILE_PAD/2+index*cell
            x2 = x1+cell
            cx = (x1+x2)/2
            matched = self.item_matches(item, query)
            have = key in self.found
            base = mix(ACCENT, SLOT, .16) if matched else SLOT
            outline = ACCENT if matched or have else None
            line = 2 if matched else 1.2 if have else 0
            background = shapes.rect(canvas, x1+2, y+5, x2-2, bottom-5, 8, base, outline, line, tags=('cell', tag))
            looks = shapes.variants(x1+2, y+5, x2-2, bottom-5, 8, (base, outline, line), (SLOT_HOVER, outline, line))
            canvas.create_image(cx, middle, image=self.icon(key, icon_size), tags=('cell', tag))
            if entry['quantity'] > 1:
                text = f'×{entry["quantity"]}'
                pill = canvas.create_text(x2-6, bottom-11, text=text, fill=color, font=('Segoe UI', 9 if big else 7, 'bold'),
                                          anchor='e', tags=('cell', tag))
                bx1, by1, bx2, by2 = canvas.bbox(pill)
                canvas.tag_lower(shapes.rect(canvas, bx1-4, by1-1, bx2+3, by2+1, 5, '#0d1015', tags=('cell', tag)), pill)
                canvas.tag_raise(pill)
                texts.append(text)
            if have:
                shapes.rect(canvas, x2-17, y+7, x2-5, y+19, 6, ACCENT, tags=('cell', tag))
                canvas.create_image(x2-11, y+13, image=icon(self, 'check', '#11151b', 9), tags=('cell', tag))
                texts.append('✓ '+display_name(item))
            self.cells[tag] = {'recipe': recipe['id'], 'key': key, 'quantity': entry['quantity'],
                               'bg': background, 'x1': x1, 'x2': x2, 'base': base, 'looks': looks}
            canvas.tag_bind(tag, '<Enter>', lambda event, t=tag: self.enter_item(t, event))
            canvas.tag_bind(tag, '<Motion>', lambda event, t=tag: self.move_item(t, event))
            canvas.tag_bind(tag, '<Leave>', lambda event: self.schedule_leave())
            canvas.tag_bind(tag, '<Button-3>', lambda event, t=tag: self.item_popup(t, event))
        self.recipe_boxes[recipe['id']] = (x, y, x+width, bottom)
        self.rendered_text[recipe['id']] = texts
        return bottom

    # ------------------------------------------------------------------ hover
    def origin_blocks(self, key, quantity=None):
        item = self.items[key]
        blocks = [('title', display_name(item) + ('   ·   '+_('×{n} requis', n=quantity) if quantity else ''))]
        recipes = ', '.join(recipe_label(cid) for cid in item.get('collections', []))
        if recipes: blocks.append(('recipe', f'{_(TIER_NAMES[item["tier"]])} · {recipes}'))
        if key in self.found:
            places = _(' et ').join(_('ton sac') if p == 'bag' else _('le coffre') for p in sorted(self.found[key], key=lambda p: p != 'bag'))
            blocks.append(('found', _('✓ Repéré dans {places} sur ta dernière capture', places=places)))
        origins = describe_origin(self.origins.get(key, []))
        if origins:
            blocks.append(('section', _('OÙ LE TROUVER')))
            for sure, heading, body in origins:
                blocks.append(('sure' if sure else 'luck', ('●  ' if sure else '○  ')+heading))
                if body: blocks.append(('body', body))
            blocks.append(('foot', _('● garanti   ○ au hasard   ·   données robrunstash du 24/11/2026')))
        else:
            blocks.append(('body', _('Origine inconnue dans les données communautaires.')))
        return blocks

    def origin_text(self, key, quantity=None):
        return '\n'.join(text for _, text in self.origin_blocks(key, quantity))

    def enter_item(self, tag, event):
        if self.hide_after:
            self.after_cancel(self.hide_after)
            self.hide_after = None
        if self.hovered != tag:
            self.paint_cell(self.hovered, False)
            self.hovered = tag
            self.paint_cell(tag, True)
        cell = self.cells[tag]
        self.tooltip.show_rich(event.x_root, event.y_root, self.origin_blocks(cell['key'], cell['quantity']))
        self.canvas.configure(cursor='hand2')

    def move_item(self, tag, event):
        if self.hovered == tag: self.tooltip.move(event.x_root, event.y_root)

    def schedule_leave(self):
        if self.hide_after: self.after_cancel(self.hide_after)
        self.hide_after = self.after(80, self.check_leave)

    def check_leave(self):
        self.hide_after = None
        if self.hovered and any(self.hovered in self.canvas.gettags(i) for i in items_under_pointer(self.canvas)):
            return
        self.leave_item()

    def leave_item(self):
        self.hide_after = None
        self.paint_cell(self.hovered, False)
        self.hovered = None
        self.tooltip.hide()
        try: self.canvas.configure(cursor='')
        except tk.TclError: pass

    def paint_cell(self, tag, active):
        cell = self.cells.get(tag)
        if cell is None: return
        try: self.canvas.itemconfigure(cell['bg'], image=cell['looks'][1 if active else 0])
        except tk.TclError: pass

    def item_popup(self, tag, event):
        cell = self.cells[tag]
        item = self.items[cell['key']]
        self.tooltip.hide()
        menu = self.item_menu
        menu.delete(0, 'end')
        menu.add_command(label=_('Toutes les recettes avec « {name} »', name=display_name(item)), command=lambda: self.show_item(item))
        menu.add_command(label=_('Ouvrir sa fiche sur robrunstash (navigateur)'),
                         command=lambda: webbrowser.open(item_page(cell['key'])))
        try: menu.tk_popup(event.x_root, event.y_root)
        finally: menu.grab_release()

    def show_recipe(self, recipe):
        self.tier.set('all')
        self.found_only.set(False)
        self.query.set(collection_title(recipe))
        self.render()

    def show_item(self, item):
        self.tier.set('all')
        self.found_only.set(False)
        self.query.set(display_name(item))
        self.render()
