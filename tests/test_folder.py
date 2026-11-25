"""Integration checks using disposable files only, never the user's captures."""
import json, logging, os, sys, tempfile
try:  # Windows writes redirected output in cp1252, which lacks characters such as arrows
    sys.stdout.reconfigure(encoding='utf-8', errors='replace'); sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass
from pathlib import Path
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC, SAMPLES, OUT = ROOT/'src', ROOT/'tests'/'samples', ROOT/'tests'/'output'
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(SRC))
import app, watcher
from cleanup import recycle_capture
from detector import Analysis, Detector

checks = []
logging.basicConfig(filename=str(OUT/'test_folder.log'), level=logging.INFO)
def passed(name):
    checks.append(name)
    print('PASS:', name, flush=True)

with tempfile.TemporaryDirectory(prefix='folder-cleanup-') as td:
    root = Path(td).resolve()
    steam = root/'Steam elsewhere'
    a = steam/'userdata/111/760/remote/4262460/screenshots'
    b = steam/'userdata/222/760/remote/4262460/screenshots'
    a.mkdir(parents=True); b.mkdir(parents=True)
    assert watcher.discover_steam_folder([steam]) in (a,b)
    old = a/'old.jpg'; Image.new('RGB',(32,32)).save(old); os.utime(old,(10,10))
    recent = b/'recent.jpg'; Image.new('RGB',(32,32)).save(recent); os.utime(recent,(20,20))
    (a/'thumbnails').mkdir(); (a/'thumbnails/new.png').write_bytes(b'ignored')
    wrong = steam/'userdata/333/760/remote/12345/screenshots'; wrong.mkdir(parents=True)
    (wrong/'new.jpg').write_bytes(b'ignored')
    assert watcher.discover_steam_folder([steam]) == b
    with patch.object(watcher, 'discover_steam_folder', return_value=b):
        assert watcher.startup_folder(saved=a,mode='auto') == (b,'auto')
        assert watcher.startup_folder(saved=a,mode='manual') == (a,'manual')
        assert watcher.startup_folder(saved=root/'missing',mode='manual') == (b,'auto')
        assert watcher.startup_folder(explicit=a) == (a,'manual')
    with patch.object(watcher, 'discover_steam_folder', return_value=None):
        assert watcher.startup_folder(saved=a) == (a,'manual')
        assert watcher.startup_folder(saved=root/'missing') == (None,'auto')
    passed('Steam elsewhere, multiple accounts, empty folders, stale saved path, manual preference')

    watch = watcher.FolderWatcher(a)
    # A complete JPEG is emitted at first sight (no second observation needed), and only once.
    assert watch.poll() == old and watch.poll() is None
    newest = a/'latest.jpg'; Image.new('RGB',(32,32)).save(newest)
    assert watcher.complete(newest) and watch.poll() == newest and watch.poll() is None
    token = watcher.fingerprint(newest)
    recycle_capture(newest, token, a)
    assert not newest.exists() and old.exists()
    assert watch.poll() is None and watch.poll() is None
    restarted = watcher.FolderWatcher(a, after=token)
    assert restarted.poll() is None and restarted.poll() is None
    new = a/'new.jpg'; Image.new('RGB',(32,32)).save(new)
    assert restarted.poll() == new and restarted.poll() is None
    # A file still being written (no JPEG end marker yet) waits until two observations agree.
    full = new.read_bytes(); new.write_bytes(full[:len(full)//2]*3)
    assert not watcher.complete(new)
    assert restarted.poll() is None and restarted.poll() == new
    new.write_bytes(b'changed while writing')
    assert restarted.poll() is None and restarted.poll() == new
    passed('Actual Windows recycling; no older-file cascade, including after restart; complete JPEGs at first sight, partial ones once stable')

    changed = a/'changed.jpg'; Image.new('RGB',(32,32)).save(changed)
    token = watcher.fingerprint(changed); changed.write_bytes(b'replaced')
    try: recycle_capture(changed, token, a); raise AssertionError('Deleted changed file')
    except OSError: pass
    try: recycle_capture(recent, watcher.fingerprint(recent), a); raise AssertionError('Deleted outside file')
    except OSError: pass
    assert recent.exists() and changed.exists()
    passed('Changed files and files outside watched folder are preserved')

    # Older settings get the new default once; a choice made afterwards is kept.
    old_settings = {'folder': 'x', 'delete_after_analysis': False, 'topmost': True}
    migrated, changed = app.migrate_settings(dict(old_settings))
    assert changed and migrated['delete_after_analysis'] is True and migrated['delete_confirmed'] and migrated['topmost']
    assert migrated['folder'] == 'x' and migrated['defaults_version'] == app.DEFAULTS_VERSION
    kept, changed = app.migrate_settings({**migrated, 'delete_after_analysis': False})
    assert not changed and kept['delete_after_analysis'] is False
    for broken in ([], None, {'defaults_version': 'x'}, {'defaults_version': True}):
        fixed, changed = app.migrate_settings(broken)
        assert changed and fixed['delete_after_analysis'] is True
    passed('Existing settings switch « Auto Recycle Bin » on once, later choice kept')

    gui_data = root/'gui'; gui_data.mkdir()
    (gui_data/'settings.json').write_text(json.dumps({'language': 'en'}), encoding='utf8')  # English texts below
    app.BASE = gui_data
    with patch.object(app.App, 'worker', lambda self: None), patch.object(app, 'startup_folder', return_value=(None,'auto')):
        gui = app.App()
    gui.withdraw(); gui.watch_active.set(False)
    try:
        assert gui.delete_after.get() and gui.watcher is None, '« Auto Recycle Bin » on by default'
        # No folder yet: explain the first F12 instead of opening a folder chooser.
        with patch.object(app.filedialog,'askdirectory', return_value='') as chooser:
            gui.request_startup_folder(); gui.update()
            chooser.assert_not_called()
            assert gui.first_capture.winfo_exists() and 'F12' in ' '.join(
                w.cget('text') for frame in gui.first_capture.winfo_children() for w in [frame, *frame.winfo_children()]
                if isinstance(w, app.tk.Label))
            # « I took a screenshot » but Steam still has no folder: manual choice, cancelled -> dialog stays.
            with patch.object(app,'discover_steam_folder', return_value=None): gui.done_first_capture()
            chooser.assert_called_once()
            assert gui.watcher is None and gui.first_capture.winfo_exists()
        # Second try, the folder now exists: found automatically, dialog closed.
        with patch.object(app,'discover_steam_folder', return_value=b), patch.object(app.filedialog,'askdirectory') as chooser:
            gui.done_first_capture(); gui.update()
            chooser.assert_not_called()
        assert gui.watcher.folder == b and gui.folder_mode == 'auto' and not gui.first_capture.winfo_exists()
        gui.watcher = None
        # Found nowhere, chosen by hand from the same dialog.
        gui.request_startup_folder(); gui.update()
        with patch.object(app,'discover_steam_folder', return_value=None), \
             patch.object(app.filedialog,'askdirectory', return_value=str(a)):
            gui.done_first_capture(); gui.update()
        assert gui.watcher.folder == a and gui.folder_mode == 'manual' and not gui.first_capture.winfo_exists()
        # The welcome screen's button runs the same search.
        gui.watcher = None
        with patch.object(app,'discover_steam_folder', return_value=b): gui.first_capture_done()
        assert gui.watcher.folder == b and gui.folder_mode == 'auto'
        with patch.object(app.filedialog,'askdirectory', return_value=str(a)):
            gui.choose_folder()
        assert gui.watcher.folder == a and gui.folder_mode == 'manual'
        with patch.object(app,'discover_steam_folder', return_value=b): gui.auto_folder()
        assert gui.watcher.folder == b and gui.folder_mode == 'auto'
        saved = json.loads((gui_data/'settings.json').read_text(encoding='utf8'))
        assert saved['folder_mode'] == 'auto' and saved['delete_after_analysis'] is True
        assert saved['delete_confirmed'] and saved['defaults_version'] == app.DEFAULTS_VERSION
        passed('No folder: first-F12 explanation, « I took a screenshot » finds it or falls back to manual choice, « Auto Recycle Bin » on by default')

        def result_for(path, stale=False):
            gui.submit(path)
            job = gui.jobs.get_nowait()
            generation, path, zone, trunk, cached, cleanup_folder = job
            result = Analysis(Image.new('RGB',(640,480)),[],None,'Test sans inventaire',.1,{})
            before = watcher.fingerprint(path) if cached is None else None
            gui.events.put(('result',(generation-1 if stale else generation,path,result,before,cleanup_folder)))
            return job

        # Even a successful analysis with zero items counts as processed.
        gui.delete_after.set(True); gui.save_settings()
        with patch.object(app,'recycle_capture') as recycle:
            result_for(old); gui.pump(); recycle.assert_not_called()
            result_for(recent, stale=True); gui.pump(); recycle.assert_not_called()
            result_for(recent); gui.delete_after.set(False); gui.pump(); recycle.assert_not_called()
        assert old.exists() and recent.exists()
        gui.delete_after.set(True)
        with patch.object(app,'recycle_capture') as recycle, patch.object(app,'write_json',side_effect=OSError('test save failure')):
            result_for(recent); gui.pump(); recycle.assert_not_called()
        assert recent.exists()
        passed('Outside folder, stale results, disabled option and failed result save never delete')

        result_for(recent); gui.pump()
        assert not recent.exists() and gui.analysis is not None
        assert 'Recycle Bin' in gui.caption.cget('text')
        gui.refresh_results(); gui.update_idletasks()
        with patch.object(gui.canvas,'winfo_width',return_value=600), patch.object(gui.canvas,'winfo_height',return_value=400):
            gui.draw_preview()
            assert gui.canvas.find_all()
        saved = json.loads((gui_data/'settings.json').read_text(encoding='utf8'))
        assert saved['delete_after_analysis'] and str(b) in saved['recycled_captures']
        gui.submit(recent)
        job = gui.jobs.get_nowait()
        assert isinstance(job[4],Image.Image) and job[5] is None
        passed('Successful GUI analysis recycles, saves preference/cutoff and retains preview/reanalysis in memory')

        failed = b/'failure.jpg'; Image.new('RGB',(32,32)).save(failed)
        with patch.object(app,'recycle_capture',side_effect=OSError('test access denied')):
            result_for(failed); gui.pump()
        assert failed.exists() and gui.analysis is not None
        assert 'could not delete' in gui.caption.cget('text')
        passed('Recycling failure keeps the capture and the displayed analysis')
    finally:
        for callback in gui.tk.splitlist(gui.tk.call('after','info')): gui.after_cancel(callback)
        gui.close()

    detector = Detector(app.DATA)
    source = SAMPLES/'storage_1440.jpg'
    disk = detector.analyze(source)
    with Image.open(source) as image: memory = detector.analyze(image)
    assert sorted((d.item['id'],d.box) for d in disk.detections) == sorted((d.item['id'],d.box) for d in memory.detections)
    assert len(disk.detections) == 17
    passed('Reanalysis from memory matches file analysis: same 17 storage items and frames')

(OUT/'test_folder.json').write_text(json.dumps({'passed':True,'checks':checks},ensure_ascii=False,indent=2),encoding='utf8')
print('ALL FOLDER/CLEANUP CHECKS PASSED',flush=True)
