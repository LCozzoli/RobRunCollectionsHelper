"""Collections Helper icon: dark rounded tile, 2x2 grid of tier cells (Basic, Purple, Gold) and a lime magnifier.

Usage: python tools/build_icon.py src/data   (writes app.ico and logo.png)"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw
OUT = Path(sys.argv[1])
S = 1024
def icon(size):
    k = 4; n = size*k; f = n/S
    img = Image.new('RGBA', (n, n), (0, 0, 0, 0))
    # background tile with vertical gradient
    grad = Image.new('RGBA', (n, n))
    top, bottom = (36, 46, 64), (14, 17, 23)
    gd = ImageDraw.Draw(grad)
    for y in range(n):
        t = y/(n-1); gd.line([(0, y), (n, y)], fill=tuple(int(a+(b-a)*t) for a, b in zip(top, bottom))+(255,))
    mask = Image.new('L', (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((int(24*f), int(24*f), n-int(24*f), n-int(24*f)), int(220*f), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((int(24*f), int(24*f), n-int(24*f), n-int(24*f)), int(220*f), outline=(58, 70, 89, 255), width=max(1, int(10*f)))
    cell, gap, x0, y0 = 300, 64, 182, 182
    colors = [(112, 220, 154), (191, 131, 255), (245, 198, 92)]
    for i, c in enumerate(colors):
        cx, cy = x0+(i % 2)*(cell+gap), y0+(i//2)*(cell+gap)
        box = [int(v*f) for v in (cx, cy, cx+cell, cy+cell)]
        inner = tuple(int(a*.22+b*.78) for a, b in zip(c, (24, 30, 41)))
        d.rounded_rectangle(box, int(70*f), fill=inner+(255,), outline=c+(255,), width=max(2, int(34*f)))
    # magnifier in the 4th cell
    mx, my, r = x0+cell+gap+130, y0+cell+gap+130, 112
    lime = (181, 239, 131, 255)
    d.line([int((mx+70)*f), int((my+70)*f), int((mx+200)*f), int((my+200)*f)], fill=lime, width=int(70*f))
    d.ellipse([int((mx+200-35)*f), int((my+200-35)*f), int((mx+200+35)*f), int((my+200+35)*f)], fill=lime)
    d.ellipse([int((mx-r)*f), int((my-r)*f), int((mx+r)*f), int((my+r)*f)], fill=(16, 19, 24, 255), outline=lime, width=int(46*f))
    return img.resize((size, size), Image.LANCZOS)
sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
big = icon(256)
big.save(OUT/'app.ico', sizes=[(s, s) for s in sizes], append_images=[icon(s) for s in sizes[:-1]])
icon(64).save(OUT/'logo.png')
if len(sys.argv) > 2: icon(512).save(sys.argv[2])  # optional large preview
