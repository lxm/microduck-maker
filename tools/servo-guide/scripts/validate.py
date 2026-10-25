#!/usr/bin/env python3
"""Validate static assets and CAD-to-servo annotations, without extra packages."""
import array,json,math,re,subprocess,sys,tempfile
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote,urlsplit
SITE=Path(__file__).resolve().parent.parent/'site'
class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        self.links.extend(v for k,v in attrs if k in ('href','src') and v)
for html in SITE.glob('*.html'):
    text=html.read_text();parser=Links();parser.feed(text)
    for link in parser.links:
        url=urlsplit(link)
        if url.scheme or url.netloc or not url.path:continue
        target=(html.parent/unquote(url.path)).resolve()
        assert target.is_relative_to(SITE.resolve()) and target.exists(),(html,link)
    assert not re.search(r'localhost|127\.0\.0\.1|/Users/|__TOKEN__|zero\.html',text),html
    assert '<meta name="viewport"' in text
    for attrs,code in re.findall(r'<script\b([^>]*)>(.*?)</script>',text,re.S):
        if 'importmap' in attrs:json.loads(code)
        elif code.strip():
            with tempfile.TemporaryDirectory() as tmp:
                f=Path(tmp)/'script.mjs';f.write_text(code)
                subprocess.run(['node','--check',str(f)],check=True)
model=json.loads((SITE/'model/model.json').read_text());binary=(SITE/'model/meshes.bin').read_bytes()
assert model['assembly_sha256']=='dba8e6d82bc196046b2811f823394797e29768eb0e2e7746ec7e4aa9ed48d3f0'
assert len(model['meshes'])==38 and len(model['instances'])==69
end=0
for m in model['meshes']:
    assert m['voff']==end and m['ioff']==m['voff']+m['nverts']*12
    end=m['ioff']+m['ntris']*12;assert end<=len(binary)
    v=array.array('f',binary[m['voff']:m['ioff']]);idx=array.array('I',binary[m['ioff']:end])
    if sys.byteorder!='little':v.byteswap();idx.byteswap()
    assert all(math.isfinite(x) for x in v) and max(idx)<m['nverts']
assert end==len(binary)
servos={}
for item in model['instances']:
    a=item['matrix'];assert len(a)==16 and all(math.isfinite(x) for x in a)
    assert all(abs(a[i]-value)<1e-8 for i,value in [(3,0),(7,0),(11,0),(15,1)])
    for i in range(3):
        for j in range(3):assert abs(sum(a[i*4+k]*a[j*4+k] for k in range(3))-int(i==j))<1e-5
    bbox=model['meshes'][item['mesh']]['bbox'];c=[(lo+hi)/2 for lo,hi in zip(*bbox)]
    anchor=[sum(a[k*4+row]*c[k] for k in range(3))+a[12+row] for row in range(3)]
    assert all(abs(x-y)<1e-4 for x,y in zip(anchor,item['anchor']))
    if item['servo']:
        assert item['id'] not in servos
        servos[item['id']]=item
assert sorted(servos)==list(range(10,15))+list(range(20,25))+list(range(30,35))
for sid in range(10,15):assert servos[sid]['anchor'][1]<0<servos[sid+10]['anchor'][1]
for hip,knee in [(12,13),(22,23)]:
    assert servos[hip]['chain']==servos[knee]['chain'] and servos[hip]['anchor'][2]>servos[knee]['anchor'][2]
for sid,prefix in [(11,'11_'),(21,'02_'),(12,'13_'),(22,'04_')]:assert servos[sid]['chain'][-1].startswith(prefix)
for html in SITE.glob('*.html'):assert 'navigator.serial' not in html.read_text() and "fetch('/api" not in html.read_text()
assert 'Creative Commons' in (SITE/'licenses/CC-BY-NC-SA-4.0.txt').read_text()
assert 'Permission is hereby granted' in (SITE/'vendor/THREE-LICENSE.txt').read_text()
print('PASS: HTML links, JavaScript syntax, 38 meshes, 69 rigid transforms, 15 IDs, anchors, left/right, hip pairs and licenses.')
