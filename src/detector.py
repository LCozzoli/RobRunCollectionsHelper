"""Local image matching for ROB RUN. No game process or network access."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json, math, os, time
import cv2
import numpy as np
from PIL import Image, ImageOps
from i18n import _

# The matching work is split across CPU threads by this module (OpenCV releases the GIL), so OpenCV's
# own internal threading is turned off to avoid oversubscription. Results do not depend on the split.
WORKERS = max(1, min(32, os.cpu_count() or 1))
cv2.setNumThreads(1)
COLORS = {'basic': '#70dc9a', 'purple': '#bf83ff', 'gold': '#f5c65c'}
LABELS = {'basic': 'BASIQUE', 'purple': 'PURPLE', 'gold': 'GOLD'}
SCALES = (.390, .398, .402, .404, .406, .410, .414)


@dataclass
class Detection:
    item: dict
    box: tuple[int, int, int, int]
    confidence: float
    location: str = 'bag'
    icon_box: tuple[int, int, int, int] | None = None
    cells: tuple[int, int, int, int] | None = None


@dataclass
class Analysis:
    image: Image.Image
    detections: list[Detection]
    inventory_box: tuple[int, int, int, int] | None
    message: str
    elapsed: float
    zones: dict = field(default_factory=dict)
    zone_labels: dict = field(default_factory=dict)


def overlap(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, x2-x1)*max(0, y2-y1)
    area = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
    return intersection / max(1, area)


def template_variants(path):
    with Image.open(path) as im:
        rgba = np.array(im.convert('RGBA'))
    result = []
    for scale in SCALES:
        t = cv2.resize(rgba, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        mask = (t[:, :, 3] > 220).astype(np.uint8)*255
        if np.count_nonzero(mask) < 30:
            continue
        gray = cv2.cvtColor(t[:, :, :3], cv2.COLOR_RGB2GRAY)
        result.append((gray, mask, t[:, :, :3]))
    return result


def masked_template(template, mask):
    """Precompute what masked TM_CCOEFF_NORMED needs from a template (binary mask)."""
    weight = (mask > 0).astype(np.float32)
    values = template.astype(np.float32)
    count = float(weight.sum())
    centred = weight*(values-float((values*weight).sum())/count)
    return centred, weight, count, float((centred*centred).sum())


def masked_ccoeff_normed(image, image_sq, prepared):
    """Same score as cv2.matchTemplate(..., TM_CCOEFF_NORMED, mask=mask), about 3.5x faster.

    OpenCV's masked path is slow; this uses three plain correlations instead:
    sum(M*T'*I) / sqrt(sum(T'^2) * (sum(M*I^2) - sum(M*I)^2/n)). The image is centred on its mean
    (the score is shift-invariant) for float precision. Flat windows, where OpenCV returns NaN/inf
    that the caller maps to -1, are -1 here."""
    centred, weight, count, energy = prepared
    numerator = cv2.matchTemplate(image, centred, cv2.TM_CCORR)
    first = cv2.matchTemplate(image, weight, cv2.TM_CCORR)
    second = cv2.matchTemplate(image_sq, weight, cv2.TM_CCORR)
    variance = second-first*first/count
    flat = variance < .5
    variance[flat] = 1.
    scores = numerator/np.sqrt(variance*energy)
    scores[flat] = -1.
    return scores


def correlation(a, b, mask):
    """Mean-centered RGB correlation inside opaque icon pixels."""
    a, b = a[mask > 0].astype(np.float32), b[mask > 0].astype(np.float32)
    a -= a.mean(axis=0)
    b -= b.mean(axis=0)
    denominator = np.linalg.norm(a)*np.linalg.norm(b)
    return float(np.sum(a*b)/denominator) if denominator > 1e-5 else 0.


CELL = 62.2  # inventory cell pitch in UI pixels at scale 1
LATTICE_MIN = .28  # grid score of an inventory; gameplay scenes stay under .23


def lattice_maps(gray):
    """Vertical and horizontal edge maps at half size, for the slot-grid search."""
    edges = cv2.Canny(gray, 12, 32) > 0
    gx = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
    vertical = np.abs(gx) > np.abs(gy)
    ev, eh = (edges & vertical).astype(np.uint8), (edges & ~vertical).astype(np.uint8)
    h, w = gray.shape[0]//2, gray.shape[1]//2
    pool = lambda a: a[:h*2, :w*2].reshape(h, 2, w, 2).max(axis=(1, 3))
    return pool(ev), pool(eh)


def _shift(a, dx, dy):
    out = np.zeros_like(a)
    h, w = a.shape
    if dx < w and dy < h: out[:h-dy, :w-dx] = a[dy:, dx:]
    return out


def lattice_scores(ev, eh, scale, rows, cols=6, tolerance=2):
    """Score of a cols x rows cell grid whose top-left corner is at each pixel (half-size maps).

    Dividers must be present on the cell boundaries and comparatively absent half a cell away, both
    vertically and horizontally, so textured scenery and item artwork do not look like a grid."""
    pitch = CELL*scale/2
    kernel = np.ones((1, 2*tolerance+1), np.uint8)
    ev = cv2.dilate(ev, kernel).astype(np.float32)
    eh = cv2.dilate(eh, kernel.T).astype(np.float32)
    tall, wide = max(3, int(round(rows*pitch))), max(3, int(round(cols*pitch)))
    vbox = cv2.boxFilter(ev, -1, (1, tall), normalize=True, borderType=cv2.BORDER_CONSTANT)
    hbox = cv2.boxFilter(eh, -1, (wide, 1), normalize=True, borderType=cv2.BORDER_CONSTANT)
    cy, cx = int(round(rows*pitch/2)), int(round(cols*pitch/2))
    on_v = sum(_shift(vbox, int(round(k*pitch)), cy) for k in range(cols+1))/(cols+1)
    off_v = sum(_shift(vbox, int(round((k+.5)*pitch)), cy) for k in range(cols))/cols
    on_h = sum(_shift(hbox, cx, int(round(j*pitch))) for j in range(rows+1))/(rows+1)
    off_h = sum(_shift(hbox, cx, int(round((j+.5)*pitch))) for j in range(rows))/rows
    return np.minimum(on_v-off_v, on_h-off_h)


def fit_lattice(gray, origin, scale, rows, cols=6):
    """Sub-pixel fit of a cols x rows grid near origin: returns ((x, y), scale) of its outer edge, or None."""
    x0, y0 = origin
    pitch0 = CELL*scale
    margin = int(pitch0*.25)+6
    x1, y1 = max(0, int(x0-margin)), max(0, int(y0-margin))
    x2 = min(gray.shape[1], int(x0+cols*pitch0*1.04+margin)); y2 = min(gray.shape[0], int(y0+rows*pitch0*1.04+margin))
    part = gray[y1:y2, x1:x2]
    if part.shape[0] < 50 or part.shape[1] < 50: return None
    edges = cv2.Canny(part, 12, 32) > 0
    gx = np.abs(cv2.Sobel(part, cv2.CV_16S, 1, 0, ksize=3)); gy = np.abs(cv2.Sobel(part, cv2.CV_16S, 0, 1, ksize=3))
    vx = (edges & (gx > gy)).mean(axis=0).astype(float)
    hy = (edges & (gy >= gx)).mean(axis=1).astype(float)
    kernel = np.ones(3)/3
    vx, hy = np.convolve(vx, kernel, 'same'), np.convolve(hy, kernel, 'same')
    best = None
    for pitch in np.arange(pitch0*.985, pitch0*1.015, .02):
        offsets = np.arange(-margin+2, margin-2, .25)
        xs = (x0-x1)+offsets[:, None]+np.arange(cols+1)[None, :]*pitch
        ys = (y0-y1)+offsets[:, None]+np.arange(rows+1)[None, :]*pitch
        a = np.interp(xs, np.arange(len(vx)), vx).mean(axis=1)
        b = np.interp(ys, np.arange(len(hy)), hy).mean(axis=1)
        i, j = int(np.argmax(a)), int(np.argmax(b))
        value = a[i]+b[j]
        if best is None or value > best[0]: best = (value, x0+offsets[i], y0+offsets[j], pitch)
    if best is None or best[0] < .3: return None
    _x, x, y, pitch = best
    # Measured divider spacing is 62.0 UI pixels at scale 1 (zones keep the nominal 62.2 x 6 = 373 width);
    # the detected edge sits half a UI pixel before the zone's outer edge.
    scale = pitch/62.
    return (x+scale*.5, y+scale*.5), scale


def grid_geometry(rgb, box, scale):
    """Fit square cell spacing to the visible dividers, in screenshot pixels."""
    x,y,x2,y2=box
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    edges=cv2.Canny(gray,12,32)>0
    vx=edges[y+4:y2-4,:].mean(axis=0)
    hy=edges[:,x+4:x2-4].mean(axis=1)
    smooth=max(3,round(scale)*2+1)
    vx=np.convolve(vx,np.ones(smooth)/smooth,mode='same')
    hy=np.convolve(hy,np.ones(smooth)/smooth,mode='same')
    expected=62.2*scale
    rows=max(1,round((y2-y)/expected))
    if rows<2:return float(x),float(y),expected,rows
    phases=np.arange(-8*scale,8*scale+.01,.5*scale)
    best=(-1.,(float(x),float(y),expected,rows))
    xs,ys=np.arange(len(vx)),np.arange(len(hy))
    for pitch in np.arange(expected*.92,expected*1.08,.125*scale):
        xx=x+phases[:,None]+np.arange(1,6)[None,:]*pitch
        yy=y+phases[:,None]+np.arange(1,rows)[None,:]*pitch
        a=np.interp(xx,xs,vx).mean(axis=1)-np.abs(phases)*.0005
        b=np.interp(yy,ys,hy).mean(axis=1)-np.abs(phases)*.0005
        i,j=int(np.argmax(a)),int(np.argmax(b))
        score=float(a[i]+b[j]-.0005*abs(pitch-expected))
        if score>best[0]:
            # Canny lies on the dark side of the separator. Shift to its centre.
            best=(score,(float(x+phases[i]+1),float(y+phases[j]+1),float(pitch),rows))
    return best[1] if best[0]>.2 else (float(x),float(y),expected,rows)


def cell_outline(icon_box, grid, image_shape):
    """Expand the recognized sprite to the whole 1x1, 1x2, 2x1, etc. tile."""
    gx,gy,pitch,rows=grid
    x1,y1,x2,y2=icon_box
    tolerance=pitch*.055
    col=max(0,min(5,math.floor((x1-gx+tolerance)/pitch)))
    row=max(0,min(rows-1,math.floor((y1-gy+tolerance)/pitch)))
    end_col=max(col+1,min(6,math.ceil((x2-gx-tolerance)/pitch)))
    end_row=max(row+1,min(rows,math.ceil((y2-gy-tolerance)/pitch)))
    inset=max(1.,pitch/76.)
    height,width=image_shape[:2]
    box=(max(0,round(gx+col*pitch+inset)),max(0,round(gy+row*pitch+inset)),
         min(width,round(gx+end_col*pitch-inset)),min(height,round(gy+end_row*pitch-inset)))
    return box,(col,row,end_col-col,end_row-row)


class Detector:
    def __init__(self, data: Path):
        self.data = data
        self.catalog = json.loads((data/'catalog.json').read_text(encoding='utf-8'))
        self.items = {x['id']: x for x in self.catalog['items']}
        self.templates = {key: template_variants(data/'icons'/f'{key}.png') for key in self.items}
        self.coarse_templates={}
        self.template_extents={}
        for key,variants in self.templates.items():
            tpl,mask,_x=variants[4]  # 0.406 is the centre of the measured UI scales.
            # Thin or hollow shapes are more sensitive to downsampling. Cover
            # both ends of the scale range for those instead of dropping recall.
            sparse=np.count_nonzero(mask)/mask.size<.55 or min(tpl.shape)<20
            indices=(0,4,6) if sparse else (4,)
            self.coarse_templates[key]=[
                (cv2.resize(variants[i][0],None,fx=.5,fy=.5,interpolation=cv2.INTER_AREA),
                 cv2.resize(variants[i][1],None,fx=.5,fy=.5,interpolation=cv2.INTER_NEAREST))
                for i in indices]
            self.template_extents[key]=(max(v[0].shape[1] for v in variants),max(v[0].shape[0] for v in variants))
        self.coarse_prepared={key:[(tpl.shape,masked_template(tpl,mask)) for tpl,mask in coarse]
                              for key,coarse in self.coarse_templates.items()}
        self.distractors = {p.stem: template_variants(p) for p in (data/'distractors').glob('*.png')}
        self.pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='match')
        self.last_found = {}  # where each heading was on the previous capture: the game UI does not move

    def lattice_at(self, maps, scale, rows, window):
        """Best grid score and position at one scale inside window=(x1, y1, x2, y2) of the half-size maps."""
        ev, eh = maps
        pitch = CELL*scale/2
        width, height = int(round(6*pitch)), int(round(rows*pitch))
        wx1, wy1, wx2, wy2 = (max(0, int(v)) for v in window)
        pad = int(pitch)+4
        x1, y1 = max(0, wx1-pad), max(0, wy1-pad)
        x2, y2 = min(ev.shape[1], wx2+width+pad), min(ev.shape[0], wy2+height+pad)
        if x2-x1 <= width or y2-y1 <= height: return -1., None
        scores = lattice_scores(ev[y1:y2, x1:x2], eh[y1:y2, x1:x2], scale, rows)
        valid = scores[wy1-y1:wy2-y1+1, wx1-x1:wx2-x1+1]  # only corners inside the window
        if valid.size == 0: return -1., None
        y, x = np.unravel_index(int(np.argmax(valid)), valid.shape)
        return float(valid[y, x]), (wx1+int(x), wy1+int(y))

    def find_inventory(self, rgb):
        """Find the bag from its slots alone (6 x 9 cells), whatever the game language.

        Inventory grids are the only places on screen with a regular lattice of square cells: their dividers
        line up every 62.2 UI pixels both ways. The bag is the rightmost such grid; a container sits on its left."""
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        maps = lattice_maps(gray)
        self._maps = (gray.shape, maps)
        height, width = gray.shape
        whole = (0, 0, maps[0].shape[1], maps[0].shape[0])
        last = self.last_found.get('bag')
        if last and last[0] == gray.shape:
            # The game UI does not move between captures: check the previous spot first.
            _x, scale, (px, py) = last
            score, pos = self.lattice_at(maps, scale, 9, (px-3, py-3, px+3, py+3))
            if score >= LATTICE_MIN and pos: return self.refine_bag(rgb, pos, scale, score)
        unit = height/1080
        scales = [round(float(v), 3) for v in np.arange(max(.45, unit*.6), min(3.2, unit*1.75)+.001, .02)]
        def search(scale):
            scores = lattice_scores(*maps, scale, 9)
            pitch = CELL*scale/2
            return scale, scores, pitch
        best = None
        for scale, scores, pitch in self.pool.map(search, scales):
            top = float(scores.max())
            if best is None or top > best[0]: best = (top, scale, scores, pitch)
        if best is None or best[0] < LATTICE_MIN: return None
        top, scale, scores, pitch = best
        # Several grids can share the scale (container + bag): keep the rightmost strong one.
        peaks, work = [], scores.copy()
        for _x in range(4):
            y, x = np.unravel_index(int(np.argmax(work)), work.shape)
            value = float(work[y, x])
            if value < max(LATTICE_MIN, top*.7): break
            peaks.append((int(x), int(y), value))
            # A grid also scores, more weakly, when shifted by a few cells: clear a whole grid width around it.
            rx, ry = int(5.5*pitch), int(4*pitch)
            work[max(0, y-ry):y+ry, max(0, x-rx):x+rx] = -1
        x, y, value = max(peaks, key=lambda peak: peak[0])
        self.last_found['bag'] = (gray.shape, scale, (x, y))
        return self.refine_bag(rgb, (x, y), scale, value)

    def refine_bag(self, rgb, pos, scale, score):
        """Exact pitch and origin, at full resolution, from all 7 vertical and 10 horizontal grid lines."""
        x, y = pos[0]*2, pos[1]*2
        height, width = rgb.shape[:2]
        fitted = fit_lattice(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (x, y), scale, 9)
        if fitted: (x, y), scale = fitted
        box = (round(x), round(y), min(width, round(x+6*CELL*scale)), min(height, round(y+9*CELL*scale)))
        if box[2]-box[0] < 150 or box[3]-box[1] < 200: return None
        return box, scale

    def find_container(self, rgb, bag):
        """The grid on the bag's left, if any: furniture storage (9 rows level with the bag) or a car trunk
        (8 rows under its tab bar, used only while the first tab is the active, lime one). Language-free."""
        if not bag: return None
        (bx, by, br, bb), scale = bag
        shape, maps = getattr(self, '_maps', (None, None))
        if maps is None or shape != rgb.shape[:2]:
            maps = lattice_maps(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
        pitch = CELL*scale
        cx = round(bx-415*scale)
        if cx < 0: return None
        near = (round((cx-.3*pitch)/2), round((by-.3*pitch)/2), round((cx+.3*pitch)/2), round((by+.3*pitch)/2))
        storage, storage_pos = self.lattice_at(maps, scale, 9, near)
        near = (near[0], round((by+.7*pitch)/2), near[2], round((by+1.3*pitch)/2))
        trunk, trunk_pos = self.lattice_at(maps, scale, 8, near)
        height, width = rgb.shape[:2]
        lime = lambda part: (part[:, :, 1].astype(int)-part[:, :, 2] > 60) & (part[:, :, 1] > 120)
        # A vehicle shows a tab bar in place of the first row: trunk tab first, cargo tab second.
        tab = rgb[round(by+11*scale):round(by+40*scale), max(0, round(cx+3*scale)):round(cx+70*scale)]
        band = rgb[by:round(by+.8*pitch), max(0, cx):round(cx+6*pitch)]
        if tab.size and float(lime(tab).mean()) >= .35:
            if trunk < LATTICE_MIN*.8: return None
            x, y = trunk_pos[0]*2, round(by+pitch)
            box = (x, y, min(width, round(x+6*pitch)), min(height, round(y+8*pitch)))
            return box, scale, True
        if band.size and float(lime(band).mean()) >= .06: return None  # the cargo tab is the active one
        if tab.size:
            gray_tab = cv2.cvtColor(tab, cv2.COLOR_RGB2GRAY)
            if float((gray_tab > 140).mean()) >= .5: return None     # trunk tab shown but not active
        if storage < LATTICE_MIN: return None
        x = storage_pos[0]*2
        box = (x, by, min(width, round(x+6*pitch)), bb)
        return box, scale, False

    def analyze(self, path: Path | Image.Image, manual_zone=None, manual_trunk=None) -> Analysis:
        started = time.monotonic()
        if isinstance(path, Image.Image):
            image = ImageOps.exif_transpose(path).convert('RGB')
        else:
            with Image.open(path) as source:
                source.load()
                image = ImageOps.exif_transpose(source).convert('RGB')
        if max(image.size) > 10000:
            raise ValueError(_('Image trop grande (maximum : 10 000 pixels).'))
        rgb = np.array(image)
        located = self.find_inventory(rgb)
        container = self.find_container(rgb, located)
        vehicle = bool(container and container[2])
        trunk = container[:2] if container else None
        regions = {}
        for key,manual,automatic in [('bag',manual_zone,located),('trunk',manual_trunk,trunk)]:
            if manual:
                w,h=image.size
                xa,ya,xb,yb=manual
                box=(round(xa*w),round(ya*h),round(xb*w),round(yb*h))
                regions[key]=(box,(box[2]-box[0])/373)
            elif automatic:
                regions[key]=automatic
        if not regions:
            return Analysis(image, [], None, 'No inventory found. Open your bag or a trunk, then press F12.', time.monotonic()-started)
        accepted=[]
        for key,(box,scale) in regions.items():
            accepted.extend(self.match_region(rgb,box,scale,key))
        boxes={key:region[0] for key,region in regions.items()}
        union=(min(b[0] for b in boxes.values()),min(b[1] for b in boxes.values()),max(b[2] for b in boxes.values()),max(b[3] for b in boxes.values()))
        counts={key:sum(d.location==key for d in accepted) for key in regions}
        labels={'bag':'Bag','trunk':'Car trunk' if vehicle else 'Storage'}
        message='  ·  '.join(f'{labels[k]} : {v}' for k,v in counts.items())+' collection item(s)'
        return Analysis(image,accepted,union,message,time.monotonic()-started,boxes,{k:labels[k] for k in boxes})

    def item_candidates(self, key, coarse, gray, region):
        variants = self.templates[key]
        candidates = []
        image, image_sq = coarse
        scores=np.full(image.shape,-1.,dtype=np.float32)
        for shape,prepared in self.coarse_prepared[key]:
            if shape[0]>=image.shape[0] or shape[1]>=image.shape[1]:continue
            match=masked_ccoeff_normed(image,image_sq,prepared)
            match[~np.isfinite(match)]=-1
            target=scores[:match.shape[0],:match.shape[1]]
            np.maximum(target,match,out=target)
        maxima=cv2.dilate(scores,np.ones((7,7),np.uint8))
        yy,xx=np.where((scores>=.58)&(scores>=maxima-1e-6))
        order=np.argsort(scores[yy,xx])[::-1][:80]
        max_width,max_height=self.template_extents[key]
        for index in order:
            x,y=int(xx[index])*2,int(yy[index])*2
            sx,sy=max(0,x-5),max(0,y-5)
            roi=gray[sy:min(gray.shape[0],y+max_height+6),sx:min(gray.shape[1],x+max_width+6)]
            # Same seven full-resolution checks and rejection thresholds as
            # before, restricted to candidates found in the coarse image.
            for tpl,mask,color in variants:
                th,tw=tpl.shape
                if th>roi.shape[0] or tw>roi.shape[1]:continue
                precise=cv2.matchTemplate(roi,tpl,cv2.TM_CCOEFF_NORMED,mask=mask)
                precise[~np.isfinite(precise)]=-1
                _x,score,_x,(dx,dy)=cv2.minMaxLoc(precise)
                if score<.86:continue
                px,py=sx+dx,sy+dy
                color_score=correlation(region[py:py+th,px:px+tw],color,mask)
                if color_score>=.82:
                    candidates.append((min(score,color_score),key,(px,py,px+tw,py+th)))
        return candidates

    def match_region(self, rgb, box, scale, location):
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= rgb.shape[1] and 0 <= y1 < y2 <= rgb.shape[0]) or scale < .25:
            raise ValueError(_('Zone invalide. Sélectionne les six colonnes de l’inventaire.'))
        region = cv2.resize(rgb[y1:y2, x1:x2], (round((x2-x1)/scale), round((y2-y1)/scale)), interpolation=cv2.INTER_AREA if scale > 1 else cv2.INTER_LINEAR)
        gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
        coarse=cv2.resize(gray,None,fx=.5,fy=.5,interpolation=cv2.INTER_AREA).astype(np.float32)
        coarse-=float(coarse.mean())
        coarse=(coarse,coarse*coarse)
        candidates = []
        # Each catalogue item is matched independently; the pool keeps the original item order.
        for found in self.pool.map(lambda key: self.item_candidates(key, coarse, gray, region), self.templates):
            candidates.extend(found)
        candidates.sort(reverse=True)
        accepted = []
        used = []
        for score, key, rect in candidates:
            if any(overlap(rect, seen) > .35 for seen in used): continue
            used.append(rect)
            # Similar collection variants must have an unambiguous winner.
            ambiguous = any(other != key and score-value < .025 and overlap(rect, r) > .5 for value, other, r in candidates)
            if ambiguous: continue
            xa, ya = max(0,rect[0]-4), max(0,rect[1]-4)
            xb, yb = min(region.shape[1],rect[2]+4), min(region.shape[0],rect[3]+4)
            small = region[ya:yb, xa:xb]
            small_gray = gray[ya:yb, xa:xb]
            # Include non-collection lookalikes as negative examples.
            for other, variants in self.distractors.items():
                if ambiguous: break
                if other.rsplit('_',1)[0] != key.rsplit('_',1)[0]: continue
                for tpl, mask, color in variants:
                    th, tw = tpl.shape
                    if th > small.shape[0] or tw > small.shape[1]: continue
                    s = cv2.matchTemplate(small_gray, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
                    s[~np.isfinite(s)] = -1
                    _x, value, _x, (dx,dy) = cv2.minMaxLoc(s)
                    if value >= score-.025 and correlation(small[dy:dy+th,dx:dx+tw],color,mask) >= score-.025:
                        ambiguous = True
                        break
            if not ambiguous:
                absolute = (round(x1+rect[0]*scale), round(y1+rect[1]*scale), round(x1+rect[2]*scale), round(y1+rect[3]*scale))
                accepted.append(Detection(self.items[key], absolute, score, location))
        accepted.sort(key=lambda d: (d.box[1]//35, d.box[0]))
        if accepted:
            grid=grid_geometry(rgb,box,scale)
            for detection in accepted:
                detection.icon_box=detection.box
                detection.box,detection.cells=cell_outline(detection.icon_box,grid,rgb.shape)
        return accepted
