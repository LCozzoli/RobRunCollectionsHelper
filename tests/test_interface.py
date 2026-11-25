"""Exercise the directory and live screenshot flow in our own Tk application."""
import ctypes, json, shutil, sys, tempfile, time
try:  # Windows writes redirected output in cp1252, which lacks characters such as arrows
    sys.stdout.reconfigure(encoding='utf-8', errors='replace'); sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass
from pathlib import Path
from PIL import Image, ImageGrab

ROOT=Path(__file__).resolve().parent.parent
SRC,SAMPLES,OUT=ROOT/'src',ROOT/'tests'/'samples',ROOT/'tests'/'output'
OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(SRC))
import app
import collections_view as cv
from collections_view import display_name

try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
except (AttributeError,OSError):pass

def texts(widget):
    result=[]
    for child in widget.winfo_children():
        try:result.append(child.cget('text'))
        except app.tk.TclError:pass
        result.extend(texts(child))
    return result

class Pointer:
    """Synthetic pointer event: hover logic is exercised without moving (or depending on) the real mouse."""
    def __init__(self,widget,x,y):
        self.x,self.y=int(x),int(y)
        self.x_root,self.y_root=widget.winfo_rootx()+self.x,widget.winfo_rooty()+self.y

def wait_for(gui,condition,seconds=35):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline and not condition():
        gui.update();time.sleep(.03)
    return condition()

with tempfile.TemporaryDirectory(prefix='tabs-check-') as td:
    base=Path(td).resolve(); shots=base/'screenshots';shots.mkdir()
    app.BASE=base
    # « Auto Recycle Bin » is on by default; keep it off here so test captures stay put. Interface in English.
    (base/'settings.json').write_text(json.dumps({'delete_after_analysis':False,'defaults_version':app.DEFAULTS_VERSION,'language':'en'}),encoding='utf8')
    gui=app.App(folder=shots)
    checks=[]
    def passed(text):checks.append(text);print('PASS:',text,flush=True)
    try:
        gui.update()
        assert [gui.tabs.tab(tab,'text') for tab in gui.tabs.tabs()]==['Detection','Collections']
        assert '?' not in texts(gui), 'no help button: help is in Options and on F1'
        passed('Exactly two tabs; old toolbar removed')
        # Name, logo and the segmented tab bar (pointer cursor, click, hover, unread badge).
        assert gui.title()=='Collections Helper' and not [t for t in texts(gui) if 'SegFault' in t] and 'Collections Helper' in texts(gui) and gui.logo.width()==38
        bar=gui.tab_bar
        assert bar.cget('cursor')=='hand2' and bar.winfo_ismapped() and bar.winfo_rooty()<gui.tabs.winfo_rooty()
        h=bar.winfo_height()//2
        bar.motion(Pointer(bar,bar.target(1)+20,h));gui.update()
        assert bar.hover==1 and not gui.tooltip.visible, 'no tooltip on the tabs'
        bar.click(Pointer(bar,bar.target(1)+20,h));gui.update()
        assert gui.tabs.select()==str(gui.collections_view)
        bar.leave();gui.tab_bar.set_badge(gui.detection_tab,'3');gui.update()
        assert bar.find_withtag('badge')
        bar.click(Pointer(bar,bar.target(0)+20,h));gui.update()
        assert gui.tabs.select()==str(gui.detection_tab) and not bar.find_withtag('badge')
        deadline=time.monotonic()+1
        while bar.anim and time.monotonic()<deadline: gui.update()
        assert abs(bar.slide-bar.target(0))<1 and bar.index_at(-5) is None and bar.index_at(bar.winfo_width()+5) is None
        passed('Collections Helper: logo, segmented tabs with pointer cursor, sliding selection, badge')

        assert 'ROB RUN Steam screenshots' in gui.folder_text.cget('text') or shots.name in gui.folder_text.cget('text')
        assert 'WATCHING' in gui.monitor.cget('text')
        gui.toggle_watch();gui.update()
        assert not gui.watch_active.get() and 'PAUSED' in gui.monitor.cget('text')
        gui.toggle_watch();gui.update()
        assert gui.watch_active.get() and gui.monitor.cget('text')=='WATCHING'
        # « Auto Recycle Bin » toggle, just left of the watch status; confirmed the first time only.
        gui.update()
        chip=gui.recycle_chip
        assert chip.winfo_ismapped() and chip.cget('text')=='Auto Recycle Bin' and chip.state_icon=='off' and chip.winfo_rootx()<gui.monitor.winfo_rootx()
        from unittest.mock import patch
        with patch.object(app.messagebox,'askyesno',return_value=False) as ask:
            chip.toggle();gui.update()
            assert ask.call_count==1 and not gui.delete_after.get() and chip.state_icon=='off'
        with patch.object(app.messagebox,'askyesno',return_value=True) as ask:
            chip.toggle();gui.update()
            assert ask.call_count==1 and gui.delete_after.get() and chip.state_icon=='on'
            chip.toggle();chip.toggle();gui.update()
            assert ask.call_count==1 and gui.delete_after.get(), 'asked only once'
            chip.toggle();gui.update()
        assert not gui.delete_after.get() and json.loads((base/'settings.json').read_text(encoding='utf8'))['delete_after_analysis'] is False
        menu_labels=[gui.options_menu.entrycget(i,'label') for i in range(gui.options_menu.index('end')+1) if gui.options_menu.type(i)!='separator']
        for wanted in ['Reanalyze the last screenshot','Change screenshot folder…','Set the bag area…','Move analyzed screenshots to the Recycle Bin','Help']:
            assert wanted in menu_labels, wanted
        # Toggles keep the same size on and off; icons instead of Unicode symbols.
        for toggle in [gui.recycle_chip, gui.top_chip, gui.zoom_chip]:
            sizes=set()
            for _ in range(2):
                toggle.var.set(not toggle.var.get());toggle.refresh();gui.update_idletasks()
                sizes.add((toggle.winfo_reqwidth(),toggle.winfo_reqheight()))
            assert len(sizes)==1, (toggle.base, sizes)
        gui.top.set(False);gui.top_chip.refresh();gui.zoom.set(True);gui.zoom_chip.refresh();gui.delete_after.set(False);gui.recycle_chip.refresh()
        symbols='●○✓✕⌕⚙⚠❚←→'
        assert not [t for t in texts(gui) if any(c in t for c in symbols)], [t for t in texts(gui) if any(c in t for c in symbols)]
        assert gui.monitor.cget('image') and gui.monitor_state=='on'
        passed('Readable source bar, pause/resume, « Auto Recycle Bin » toggle (confirmed once), actions in Options')

        gui.tabs.select(gui.collections_view);gui.update()
        view=gui.collections_view
        assert len(view.visible_recipes)==34 and view.paged and view.page==0
        assert [r['tier'] for r in view.drawn_recipes]==['basic']*14
        assert not hasattr(view,'note') and gui.footer_bar.winfo_manager()=='', 'no description lines under Collections'
        pages={}
        for page in (0,1):
            view.set_page(page);gui.update()
            prices=[view.canvas.itemcget(i,'text') for i in view.canvas.find_all() if view.canvas.type(i)=='text' and '/ collection' in view.canvas.itemcget(i,'text')]
            pages[page]=dict(boxes=dict(view.recipe_boxes),texts=dict(view.rendered_text),
                             cells=[dict(c) for c in view.cells.values()],prices=prices,layout=view.layout,
                             drawn=[r['id'] for r in view.drawn_recipes])
        assert pages[0]['drawn']==list(range(14)) and pages[1]['drawn']==list(range(14,34))
        assert pages[0]['prices']==['$50,000 / collection'] and pages[1]['prices']==['$150,000 / collection','$500,000 / collection']
        gui.event_generate('<Left>');gui.update()
        assert view.page==0
        gui.event_generate('<Next>');gui.update()
        assert view.page==1
        view.set_page(0);gui.update()
        passed('With « All »: page 1 Basic, page 2 Purple + Gold, switched by the pager or ← → / PgUp PgDn')
        all_cells=pages[0]['cells']+pages[1]['cells']
        all_texts={**pages[0]['texts'],**pages[1]['texts']}
        assert len(view.photos)>=len({c['key'] for c in pages[0]['cells']})
        for recipe in view.recipes:
            cells=[c for c in all_cells if c['recipe']==recipe['id']]
            assert [(c['key'],c['quantity']) for c in cells]==[(e['id'],e['quantity']) for e in recipe['items']]
            for entry in recipe['items']:
                assert view.origin_text(entry['id'],entry['quantity']).startswith(display_name(view.items[entry['id']]))
                if entry['quantity']>1: assert f'×{entry["quantity"]}' in all_texts[recipe['id']]
        assert len({c['key'] for c in all_cells})==108
        passed('All 34 recipes, 108 illustrated objects and required quantities match the catalogue')
        assert [name for _,name,_ in view.poi_drawn]==['Cafe','GasStation','AmmunitionShop','GunShop','Jewelery','Pharmacy','Electronics']
        assert all((app.DATA/'poi'/f'{name}.png').exists() for _,name,_ in view.poi_drawn)
        assert len(view.canvas.find_withtag('poi'))==14  # chip + icon per POI
        passed('Basic collections grouped by game POI icon (bar, gas station, equipment, guns, jewelry, pharmacy, electronics)')
        from collections_view import recipe_label
        assert [recipe_label(i) for i in (0,6,7,13,14,23,24,33)]==['Base 01','Base 07','Additional 08','Additional 14','Special 01','Special 10','Unique 01','Unique 10']
        boxes=pages[0]['boxes']
        for slot in range(7):
            assert abs(boxes[slot][1]-boxes[slot+7][1])<1 and boxes[slot][0]<boxes[slot+7][0]
        for page in (0,1):
            ordered=sorted(pages[page]['boxes'].values())
            for a in range(len(ordered)):
                for b in range(a+1,len(ordered)):
                    p,q=ordered[a],ordered[b]
                    assert p[2]<=q[0] or q[2]<=p[0] or p[3]<=q[1] or q[3]<=p[1], (p,q)
            sizes={(round(b[2]-b[0]),round(b[3]-b[1])) for b in pages[page]['boxes'].values()}
            assert len(sizes)==1, ('tiles must share one size on a page', sizes)
        passed('Uniform tiles: Basic base 01-07 beside additional 08-14, Purple and Gold, crate values, no overlap')
        assert set(view.origins)==set(view.items)
        text=view.origin_text('alcohol_1',1)
        assert 'WHERE TO FIND IT' in text and 'Hotel Bartender' in text and 'Strip Club (7)' in text and 'more places' in text
        assert 'Containers searched' in view.origin_text('baseball_card_1')
        tag=next(t for t,c in view.cells.items() if c['key']=='alcohol_1')
        x1,y1,x2,y2=view.canvas.bbox(tag)
        gui.update();view.enter_item(tag,Pointer(view.canvas,(x1+x2)/2,y1+20-view.canvas.canvasy(0)))
        assert view.hovered==tag and 'Hotel Bartender' in view.tooltip.text() and view.tooltip.window.winfo_ismapped()
        assert view.canvas.itemcget(view.cells[tag]['bg'],'image')==str(view.cells[tag]['looks'][1])
        polygons=[i for c in (view.canvas,gui.cards_canvas,gui.canvas) for i in c.find_all() if c.type(i) in ('polygon','oval')]
        assert not polygons, 'rounded shapes must be antialiased images, not Tk polygons'
        # A spurious Leave (Windows sends one when a window appears) must not drop the hover: no flicker loop.
        real=cv.items_under_pointer
        cv.items_under_pointer=lambda canvas:canvas.find_withtag(tag)
        try:
            for _ in range(3):
                view.canvas.event_generate('<Leave>');wait_for(gui,lambda:False,.2)
                assert view.hovered==tag and view.tooltip.visible and view.tooltip.window.winfo_ismapped()
            cv.items_under_pointer=lambda canvas:()
            view.canvas.event_generate('<Leave>');wait_for(gui,lambda:False,.2)
            assert view.hovered is None and not view.tooltip.visible
        finally:
            cv.items_under_pointer=real
        view.leave_item();gui.update()
        assert view.hovered is None and not view.tooltip.window.winfo_ismapped()
        passed('Origins for all 108 objects; hovering shows where to find it, without flicker on spurious Leave events')
        for tier,count in [('basic',14),('purple',10),('gold',10)]:
            view.tier.set(tier);view.render();gui.update()
            assert len(view.visible_recipes)==count
            assert all(recipe['tier']==tier for recipe in view.visible_recipes)
            assert view.tier_buttons[tier].cget('text').endswith(str(count))
        # Search covers every game language and ignores accents: the French name typed without its accent.
        view.tier.set('all');view.query.set('electronique');view.render();gui.update()
        assert {r['id'] for r in view.visible_recipes}==set(view.items['electronics_box_1']['collections'])
        assert any(c['key']=='electronics_box_1' and c['base']!=cv.PANEL for c in view.cells.values())
        assert view.clear_button.winfo_ismapped() and not view.placeholder.winfo_ismapped()
        view.query.set('no-matching-collection-xyz');view.render()
        assert not view.visible_recipes and 'No collection matches' in ' '.join(view.canvas.itemcget(i,'text') for i in view.canvas.find_withtag('empty'))
        view.clear_search();gui.update()
        assert view.query.get()=='' and len(view.visible_recipes)==34 and view.placeholder.winfo_ismapped()
        assert str(view.found_button.cget('state'))=='disabled'
        passed('Tier filters with counts, accent-insensitive search, clear button/placeholder and empty results')
        view.set_page(1);view.canvas.yview_moveto(1);gui.update()
        assert view.canvas.yview()[1]>.99
        last=view.recipe_boxes[33]
        assert view.canvas.canvasy(0)<last[3] and last[1]<view.canvas.canvasy(view.canvas.winfo_height())
        view.set_page(0);gui.update()
        passed('Scrolling reaches the last Gold recipe')

        # Keep Collections selected while the real worker processes a new screenshot.
        capture=shots/'new.jpg';shutil.copy2(SAMPLES/'storage_1440.jpg',capture)
        assert wait_for(gui,lambda:gui.analysis is not None)
        gui.update()
        assert len(gui.analysis.detections)==17
        assert gui.tabs.select()==str(view) and len(view.visible_recipes)==34
        assert capture.exists() and not gui.delete_after.get()
        badge=gui.tabs.tab(gui.detection_tab,'text')
        assert badge.startswith('Detection') and '●' in badge and '16' in badge, badge
        detected={d.item['id'] for d in gui.analysis.detections}
        assert set(view.found)==detected
        assert str(view.found_button.cget('state'))=='normal'
        view.found_only.set(True);view.render();gui.update()
        assert view.visible_recipes and all(any(e['id'] in detected for e in r['items']) for r in view.visible_recipes)
        assert any(t.startswith('✓ ') for r in view.visible_recipes for t in view.rendered_text.get(r['id'],[]))
        found_key=next(iter(detected))
        assert 'Spotted in' in view.origin_text(found_key)
        view.found_only.set(False);view.render();gui.update()
        passed('Detection continues in Collections: tab badge, detected items ticked, "Spotted" filter')

        gui.tabs.select(gui.detection_tab);gui.update()
        assert gui.tabs.tab(gui.detection_tab,'text')=='Detection'
        assert gui.footer_bar.winfo_manager()=='pack', 'hint line back on Detection'
        side_region=[float(v) for v in str(gui.cards_canvas.cget('scrollregion')).split()]
        assert (gui.sets_scrollbar.winfo_manager()=='pack')==(side_region[3]>gui.cards_canvas.winfo_height()+1)
        assert len(gui.canvas.find_withtag('detected-cell'))==17
        assert not [i for i in gui.canvas.find_all() if gui.canvas.type(i)=='text'], 'no zone label over the capture'
        from PIL import ImageStat
        plain,dimmed=gui.preview_plain.convert('L'),gui.preview_image.convert('L')
        ox,oy=gui.preview_origin
        for box,_ in gui.canvas_targets:
            cx,cy=int((box[0]+box[2])/2-ox),int((box[1]+box[3])/2-oy)
            assert dimmed.getpixel((cx,cy))==plain.getpixel((cx,cy)), 'kept cells keep their brightness'
        assert ImageStat.Stat(dimmed).mean[0]<ImageStat.Stat(plain).mean[0]*.9, 'other cells are darkened'
        assert gui.status.cget('text').startswith('16 collection items')
        assert gui.tier_chips['gold'].count+gui.tier_chips['purple'].count+gui.tier_chips['basic'].count==16
        assert gui.location_chips['trunk'].count==16
        gui.locations['trunk'].set(False);gui.refresh_results();gui.update()
        assert not gui.canvas.find_withtag('detected-cell')
        side=gui.cards_canvas
        assert 'hidden by the filters' in ' '.join(side.itemcget(i,'text') for i in side.find_withtag('message'))
        assert gui.count_label.cget('text')=='SETS IN PROGRESS  ·  0'
        gui.location_chips['trunk'].toggle();gui.update()
        assert gui.locations['trunk'].get() and len(gui.canvas.find_withtag('detected-cell'))==17
        passed('Returning clears the badge, 17 frames, no zone labels, unkept cells dimmed; chips filter by location')

        sets,found=gui.collection_sets()
        detected_ids={d.item['id'] for d in gui.analysis.detections}
        assert set(found)==detected_ids
        expected=[r['id'] for r in view.recipes if any(e['id'] in detected_ids for e in r['items'])]
        assert sorted(gui.set_order)==sorted(expected) and gui.count_label.cget('text')==f'SETS IN PROGRESS  ·  {len(expected)}'
        ratios=[len(h)/len(r['items']) for r,h in sets]
        assert ratios==sorted(ratios,reverse=True), 'most complete sets first'
        progress=[side.itemcget(i,'text') for i in side.find_withtag('set-progress')]
        assert progress==[f'{len(h)}/{len(r["items"])}' for r,h in sets]
        slots_found=sum(1 for v in gui.set_slots.values() for _,_,got in v if got)
        slots_missing=sum(1 for v in gui.set_slots.values() for _,_,got in v if not got)
        assert slots_found==sum(len(h) for _,h in sets) and slots_missing==sum(len(r['items'])-len(h) for r,h in sets)
        assert ('basic' in [r['tier'] for r,_ in sets]) and any(len(h)==len(r['items']) for r,h in sets)
        slot_texts={side.itemcget(i,'text') for i in side.find_withtag('slot') if side.type(i)=='text'}
        assert not slot_texts & {'S','C','S+C'}, slot_texts
        gui.show_recipe(sets[0][0]);gui.update()
        assert gui.tabs.select()==str(view) and [r['id'] for r in view.visible_recipes]==[sets[0][0]['id']]
        view.query.set('');view.render();gui.tabs.select(gui.detection_tab);gui.update()
        passed('Sets en cours: every touched collection, found items lit and missing ones dimmed, most complete first')

        box,detection=next((b,d) for b,d in gui.canvas_targets if d.item['tier']=='gold')
        gui.update();gui.preview_motion(Pointer(gui.canvas,(box[0]+box[2])/2,(box[1]+box[3])/2))
        key=detection.item['id']
        same=sum(1 for _,d in gui.canvas_targets if d.item['id']==key)
        assert gui.hover_key==key and len(gui.canvas.find_withtag('hover-cell'))==same
        slots=gui.set_slots[key]
        assert slots and all(side.itemcget(b,'image')==str(looks[1]) for b,looks,_ in slots)
        gui.preview_hover(None)
        assert not gui.canvas.find_withtag('hover-cell') and all(side.itemcget(b,'image')==str(looks[0]) for b,looks,_ in slots)
        gui.slot_hover(key,True)
        assert len(gui.canvas.find_withtag('hover-cell'))==same
        gui.slot_leave()
        assert not gui.canvas.find_withtag('hover-cell')
        passed('Hovering a frame highlights the item in its sets, and hovering a set item frames it on the capture')

        gui.collection_details(view.items['electronics_box_1']);gui.update()
        assert gui.tabs.select()==str(view)
        assert {r['id'] for r in view.visible_recipes}==set(view.items['electronics_box_1']['collections'])
        passed('Detected item opens its matching recipes in Collections')
        view.query.set('');view.render();gui.update()
        for size in ['900x640','1120x780']:
            gui.geometry(size);wait_for(gui,lambda:False,.5)
            assert view.columns==2
            assert view.search.winfo_width()>60
            for box in view.recipe_boxes.values():
                assert box[2]<=view.canvas.winfo_width()
            for cell in view.cells.values():
                badge=[i for i in view.canvas.find_withtag(next(t for t,c in view.cells.items() if c is cell)) if view.canvas.type(i)=='polygon']
                for item in badge: assert view.canvas.bbox(item)[2]<=cell['x2']+2
        passed('Directory fits both minimum and default window sizes')
        for size in ['900x640','1120x780']:
            gui.geometry(size)
            for page in (0,1):
                view.set_page(page);wait_for(gui,lambda:False,.4)
                region=[float(v) for v in str(view.canvas.cget('scrollregion')).split()]
                assert (view.scrollbar.winfo_manager()=='pack')==(region[3]>view.canvas.winfo_height()+1), (size,page,region,view.canvas.winfo_height())
        view.set_page(0)
        passed('Collections scrollbar shown only when a page is taller than the window')
        if gui.winfo_screenwidth()>=1920 and gui.winfo_screenheight()>=1080:
            gui.geometry('1920x1000+0+0');wait_for(gui,lambda:False,.8)
            fits=[]
            for page in (0,1):
                view.set_page(page);gui.update()
                region=[float(v) for v in str(view.canvas.cget('scrollregion')).split()]
                print('1920x1080 page',page+1,':',view.layout,'contenu',region[3],'visible',view.canvas.winfo_height(),'dpi',round(gui.winfo_fpixels('1i')),flush=True)
                fits.append(region[3]<=view.canvas.winfo_height() and view.layout[0]=='row')
                assert view.scrollbar.winfo_manager()=='', 'scrollbar hidden when the page fits'
                assert view.layout[1]>64, ('bigger tiles at 1920x1080', view.layout)
            view.set_page(0)
            if round(gui.winfo_fpixels('1i'))<=100:
                assert all(fits)
                passed('At 1920x1080 each page (Basic, then Purple + Gold) is fully visible without scrolling')
            gui.geometry('1120x780');wait_for(gui,lambda:False,.5)

        if sys.platform=='win32':
            gui.geometry('1120x780');gui.update()
            hwnd=ctypes.windll.user32.GetAncestor(gui.winfo_id(),2)
            view.found_only.set(True);view.render();gui.update()
            ImageGrab.grab(window=hwnd).save(OUT/'collections.png')
            view.found_only.set(False);view.render()
            gui.tabs.select(gui.detection_tab);gui.update()
            ImageGrab.grab(window=hwnd).save(OUT/'detection.png')
        (OUT/'test_interface.json').write_text(json.dumps({'passed':True,'checks':checks},ensure_ascii=False,indent=2),encoding='utf8')
    finally:
        for callback in gui.tk.splitlist(gui.tk.call('after','info')):gui.after_cancel(callback)
        gui.close();gui.worker_thread.join(5)

    # Window size and position are remembered between launches, including on a monitor left of the main one.
    saved=json.loads((base/'settings.json').read_text(encoding='utf8'))
    assert 'geometry' in saved and saved['zoom'] is True
    assert app.App.parse_geometry('1120x780+-1225+141')==(1120,780,-1225,141)
    assert app.App.parse_geometry('1120x780+22+-8')==(1120,780,22,-8)
    assert app.App.parse_geometry('1120x780-10+20')==(1120,780,None,None)
    assert app.App.parse_geometry('nonsense') is None and app.App.parse_geometry(None) is None
    for odd in ['1120x780+-1225+141','900x640+-99999+-99999','1120x780-10+20','abc']:
        saved['geometry']=odd
        (base/'settings.json').write_text(json.dumps(saved),encoding='utf8')
        probe=app.App(folder=shots)
        try:
            probe.update()
            assert probe.winfo_width()>=900
        finally:
            # Let the catalogue finish loading (its timer must still run), then stop the timers and close.
            wait_for(probe,lambda:probe.catalog_ready,30)
            for callback in probe.tk.splitlist(probe.tk.call('after','info')):probe.after_cancel(callback)
            probe.close();probe.worker_thread.join(5)
    saved['geometry']='1120x780+40+60'
    (base/'settings.json').write_text(json.dumps(saved),encoding='utf8')
    passed('Saved window positions on any monitor (negative coordinates) or unreadable ones never block startup')
    reopened=app.App(folder=shots)
    try:
        reopened.update()
        assert reopened.winfo_width()>=900
        assert wait_for(reopened,lambda:reopened.catalog_ready)
        passed('Window geometry and zoom preference persist across launches')
    finally:
        for callback in reopened.tk.splitlist(reopened.tk.call('after','info')):reopened.after_cancel(callback)
        reopened.close();reopened.worker_thread.join(5)

    # Languages: every interface text translated, game item names and places in the game's languages.
    import i18n
    table=json.loads((SRC/'data/i18n.json').read_text(encoding='utf8'))
    assert set(table)==set(i18n.LANGUAGES)-{'fr'}
    keys=set(table['en'])
    assert all(set(t)==keys for t in table.values()), 'every language has every text'
    catalog=json.loads((SRC/'data/catalog.json').read_text(encoding='utf8'))
    assert all(set(item['names'])==set(i18n.GAME_LANGUAGES) for item in catalog['items'])
    assert i18n._code('fr-FR')=='fr' and i18n._code('French')=='fr' and i18n._code('schinese')=='zh' and i18n._code('xx') is None
    steam=Path(td)/'steam';(steam/'steamapps').mkdir(parents=True)
    (steam/'steamapps'/'appmanifest_4262460.acf').write_text('"AppState"\n{\n\t"UserConfig"\n\t{\n\t\t"language"\t\t"german"\n\t}\n}',encoding='utf8')
    from unittest.mock import patch
    with patch('watcher.steam_roots',return_value=[steam]):
        assert i18n._steam_language()=='de'
    source={k for k in keys if all(t[k]!=k for t in table.values())}  # texts that every language translates
    key=lambda english: next(k for k,v in table['en'].items() if v==english)  # translation key of an English text
    for code in ['en','fr','ru','zh']:
        words=(lambda k: k) if code=='fr' else (lambda k: table[code][k])
        untranslated=set() if code=='fr' else source
        saved=json.loads((base/'settings.json').read_text(encoding='utf8'));saved['language']=code
        (base/'settings.json').write_text(json.dumps(saved),encoding='utf8')
        other=app.App(folder=shots)
        try:
            assert wait_for(other,lambda:other.catalog_ready)
            other.update()
            shown=[t for t in texts(other) if t]
            left=[t for t in shown if t in untranslated]
            assert not left, (code,left)
            assert other.tabs.tab(other.detection_tab,'text')==words(key('Detection'))
            labels=[other.options_menu.entrycget(i,'label') for i in range(other.options_menu.index('end')+1) if other.options_menu.type(i)!='separator']
            assert words(key('Language')) in labels and words(key('Help')) in labels
            other.tabs.select(other.collections_view);other.update()
            blocks=other.collections_view.origin_blocks('alcohol_1',2)
            name=catalog['items'][0]['names'].get(code) or catalog['items'][0]['names']['en']
            assert blocks[0][1].startswith(name) and words(key('WHERE TO FIND IT')) in [b[1] for b in blocks], (code,blocks[:3])
            if code=='ru': assert any('Минимаркет' in b[1] for b in blocks), blocks
            if code=='zh':
                # Changing language relaunches a fresh instance: no PyInstaller variables inherited (its temporary
                # folder disappears with this window, which made the new one say « Catalogue unavailable »).
                import subprocess
                with patch.dict(app.os.environ,{'_MEIPASS2':'x','_PYI_APPLICATION_HOME_DIR':'y'}), \
                     patch.object(app.messagebox,'askyesno',return_value=True), patch.object(subprocess,'Popen') as popen, \
                     patch.object(other,'close') as closing:
                    other.language.set('de');other.change_language()
                env=popen.call_args.kwargs['env']
                assert env['PYINSTALLER_RESET_ENVIRONMENT']=='1' and not any(k.startswith(('_MEI','_PYI')) for k in env)
                assert closing.called and json.loads((base/'settings.json').read_text(encoding='utf8'))['language']=='de'
        finally:
            for callback in other.tk.splitlist(other.tk.call('after','info')):other.tk.call('after','cancel',callback)
            other.close();other.worker_thread.join(5)
    passed('13 languages: automatic choice (game, Steam, Windows), every text translated, item names and places from the game')
    (OUT/'test_interface.json').write_text(json.dumps({'passed':True,'checks':checks},ensure_ascii=False,indent=2),encoding='utf8')
print('ALL TAB CHECKS PASSED',flush=True)
