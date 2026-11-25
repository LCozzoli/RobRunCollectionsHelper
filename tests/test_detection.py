"""Recognition on the sample screenshots: the expected objects in the expected zones, also with every panel title
and tab caption painted over (the bag and the trunk are found from their slots, whatever the game language)."""
import json, sys, time
try:  # Windows writes redirected output in cp1252, which lacks some characters
    sys.stdout.reconfigure(encoding='utf-8', errors='replace'); sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC, SAMPLES, OUT = ROOT/'src', ROOT/'tests'/'samples', ROOT/'tests'/'output'
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(SRC))
from detector import Detector

EXPECTED = json.loads((SAMPLES/'expected.json').read_text(encoding='utf-8'))


def identity(result):
    return {'objects': sorted([d.item['id'], d.location] for d in result.detections), 'zones': result.zone_labels}


def without_titles(path, result):
    """The same capture with the text above each grid painted over."""
    image = Image.open(path).convert('RGB')
    draw = ImageDraw.Draw(image)
    for key, (x1, y1, x2, y2) in result.zones.items():
        scale = (x2-x1)/373
        vehicle = result.zone_labels.get(key) == 'Car trunk'
        top = y1-round((62 if vehicle else 0)*scale)
        draw.rectangle((x1, top-round(48*scale), x2, top-round(4*scale)), fill=(44, 45, 44))
        if vehicle:  # tab captions, keeping the lime active tab
            draw.rectangle((x1+round(20*scale), top+round(16*scale), x1+round(60*scale), top+round(36*scale)), fill=(181, 230, 50))
            draw.rectangle((x1+round(95*scale), top+round(16*scale), x1+round(170*scale), top+round(36*scale)), fill=(40, 40, 40))
    blank = OUT/(path.stem+'-no-text.jpg')
    image.save(blank, quality=92)
    return blank


detector = Detector(SRC/'data')
report, total = [], 0.
for name, expected in EXPECTED.items():
    path = SAMPLES/name
    started = time.perf_counter()
    result = detector.analyze(path)
    seconds = time.perf_counter()-started
    total += seconds
    assert identity(result) == expected, f'{name}: {identity(result)} != {expected}'
    blank = without_titles(path, result)
    try:
        assert identity(detector.analyze(blank)) == expected, f'{name}: result depends on the titles'
    finally:
        blank.unlink()
    print(f'PASS: {name:<20} {len(expected["objects"]):>2} objects  {seconds:.2f} s', flush=True)
    report.append({'sample': name, 'objects': len(expected['objects']), 'seconds': round(seconds, 2)})
(OUT/'test_detection.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(f'ALL DETECTION CHECKS PASSED  ({total:.2f} s for {len(EXPECTED)} screenshots)', flush=True)
