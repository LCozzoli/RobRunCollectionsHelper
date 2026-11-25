"""Small antialiased UI icons drawn with PIL (no Unicode symbols: their size varies between glyphs and fonts)."""
import math
from PIL import Image, ImageDraw, ImageColor, ImageTk

SCALE = 4


def _mask(name, w, h):
    s = SCALE
    m = Image.new('L', (w*s, h*s), 0)
    d = ImageDraw.Draw(m)
    W, H = w*s, h*s
    u = min(W, H)/16  # drawing unit: icons are designed on a 16x16 grid

    def line(points, width):
        pts = [(x*u+(W-16*u)/2, y*u+(H-16*u)/2) for x, y in points]
        d.line(pts, fill=255, width=int(width*u), joint='curve')
        r = width*u/2
        for x, y in (pts[0], pts[-1]):
            d.ellipse((x-r, y-r, x+r, y+r), fill=255)

    def ellipse(cx, cy, r, fill=255, width=0):
        ox, oy = (W-16*u)/2, (H-16*u)/2
        box = (ox+(cx-r)*u, oy+(cy-r)*u, ox+(cx+r)*u, oy+(cy+r)*u)
        if width: d.ellipse(box, outline=fill, width=int(width*u))
        else: d.ellipse(box, fill=fill)

    if name == 'on':        # filled dot, same outer size as 'off'
        ellipse(8, 8, 5.5)
    elif name == 'off':     # ring
        ellipse(8, 8, 5.5, width=1.8)
    elif name == 'check':
        line([(3.2, 8.4), (6.6, 11.6), (12.8, 4.6)], 2.2)
    elif name == 'close':
        line([(4, 4), (12, 12)], 2); line([(12, 4), (4, 12)], 2)
    elif name == 'search':
        ellipse(7, 7, 4.6, width=1.9); line([(10.4, 10.4), (13.6, 13.6)], 2.2)
    elif name == 'pause':
        d.rounded_rectangle((W/2-5*u, H/2-5.5*u, W/2-1.5*u, H/2+5.5*u), u, fill=255)
        d.rounded_rectangle((W/2+1.5*u, H/2-5.5*u, W/2+5*u, H/2+5.5*u), u, fill=255)
    elif name == 'warn':
        cx, top, bottom = W/2, H/2-6.5*u, H/2+6*u
        d.polygon([(cx, top), (cx+7*u, bottom), (cx-7*u, bottom)], fill=255)
        d.rounded_rectangle((cx-1*u, H/2-2.5*u, cx+1*u, H/2+2*u), u, fill=0)
        d.ellipse((cx-1.1*u, H/2+3*u, cx+1.1*u, H/2+5.2*u), fill=0)
    elif name == 'gear':
        cx, cy = W/2, H/2
        for i in range(8):
            a = i*math.pi/4
            pts = []
            for da, r in ((-.2, 4.6), (-.15, 7.2), (.15, 7.2), (.2, 4.6)):
                pts.append((cx+math.cos(a+da)*r*u, cy+math.sin(a+da)*r*u))
            d.polygon(pts, fill=255)
        ellipse(8, 8, 5.4)
        ellipse(8, 8, 2.3, fill=0)
    elif name == 'left':
        line([(10, 3.5), (5.5, 8), (10, 12.5)], 2)
    elif name == 'right':
        line([(6, 3.5), (10.5, 8), (6, 12.5)], 2)
    else:
        raise KeyError(name)
    return m.resize((w, h), Image.LANCZOS)


def image(name, color, size=14, width=None):
    """Returns a PIL RGBA image of the icon."""
    w, h = width or size, size
    shape = Image.new('RGBA', (w, h), ImageColor.getrgb(color)+(255,))
    shape.putalpha(_mask(name, w, h))
    return shape


def icon(master, name, color, size=14, gap=0):
    """Cached Tk image of the icon, with `gap` transparent pixels on its right (space before a label's text)."""
    root = master._root()
    cache = root.__dict__.setdefault('_icon_cache', {})
    key = (name, color, size, gap)
    if key not in cache:
        shape = image(name, color, size)
        if gap:
            padded = Image.new('RGBA', (size+gap, size), (0, 0, 0, 0))
            padded.paste(shape, (0, 0))
            shape = padded
        cache[key] = ImageTk.PhotoImage(shape, master=root)
    return cache[key]
