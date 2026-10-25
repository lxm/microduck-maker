#!/usr/bin/env python3
"""Extract saved display meshes and native instance transforms from HD v2.1 CAD.
Requires sldkit==0.2.0, numpy; source ZIP remains outside this repository.
"""
import argparse, hashlib, json
from pathlib import Path, PureWindowsPath
import numpy as np
import sldkit

NAMES={10:('右髋偏航','right_hip_yaw'),11:('右髋侧摆','right_hip_roll'),12:('右髋俯仰','right_hip_pitch'),13:('右膝','right_knee'),14:('右踝','right_ankle'),20:('左髋偏航','left_hip_yaw'),21:('左髋侧摆','left_hip_roll'),22:('左髋俯仰','left_hip_pitch'),23:('左膝','left_knee'),24:('左踝','left_ankle'),30:('颈部俯仰','neck_pitch'),31:('头部俯仰','head_pitch'),32:('头部左右转','head_yaw'),33:('头部侧倾','head_roll'),34:('嘴','mouth')}
DETAILS={10:'躯干底座内，机器人右侧的竖轴舵机。',20:'躯干底座内，机器人左侧的竖轴舵机。',11:'右髋 yaw-roll 转接件内，连接躯干与右大腿。',21:'左髋 yaw-roll 转接件内，连接躯干与左大腿。',12:'右大腿双舵机组件中，靠近髋部的那颗；同板另一颗是 13。',13:'右大腿双舵机组件中，靠近膝盖的那颗；同板另一颗是 12。',22:'左大腿双舵机组件中，靠近髋部的那颗；同板另一颗是 23。',23:'左大腿双舵机组件中，靠近膝盖的那颗；同板另一颗是 22。',14:'右小腿组件内，连接右脚踝，最靠近右脚掌。',24:'左小腿组件内，连接左脚踝，最靠近左脚掌。',30:'颈根双舵机组件中，靠近躯干的下端舵机。',31:'颈根双舵机组件中，靠近头部的上端舵机。',32:'头部 yaw-roll 组件内，输出轴朝上下，负责左右转头。',33:'头部后侧，输出轴朝前后，负责头部侧倾。',34:'头部嘴侧的横轴舵机，驱动下巴开合。'}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('--out',type=Path,default=Path(__file__).resolve().parent.parent/'site'/'model');a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 files={p.name.lower():p for p in a.source.rglob('*') if p.is_file()}
 top=next(p for p in files.values() if p.name.startswith('00-') and p.suffix.upper()=='.SLDASM')
 cache={};chunks=[];offset=0;instances=[];issues=[];assembly_audit=[]
 # CAD axes: -Y = forward (camera lens), -X = robot left, -Z = up.
 basis=np.array([[0,-1,0,0],[-1,0,0,0],[0,0,-1,0],[0,0,0,1]],float)
 def geom(p):
  nonlocal offset
  if p.name in cache:return cache[p.name]
  print('mesh',p.name,flush=True)
  r=sldkit.decode_geometry_file(str(p));vs=[];fs=[];n=0
  for t in r.geometry.model.tessellations if r.geometry else []:
   if not t.vertices or not t.triangles:continue
   if t.source_object and t.source_object.visible is False:continue
   v=np.asarray(t.vertices,dtype='<f4');f=np.asarray(t.triangles,dtype='<u4')
   if not np.isfinite(v).all() or f.max()>=len(v):raise ValueError('Invalid display mesh')
   vs.append(v);fs.append(f+n);n+=len(v)
  if not vs:
   issues.append({'part':p.name,'reason':'no saved tessellation','status':str(r.status)});cache[p.name]=None;return None
  v=np.concatenate(vs);f=np.concatenate(fs)
  entry=dict(name=p.name,voff=offset,nverts=len(v),ntris=len(f),bbox=[v.min(axis=0).tolist(),v.max(axis=0).tolist()],sha256=hashlib.sha256(p.read_bytes()).hexdigest(),status=str(r.status))
  vb=v.tobytes();chunks.append(vb);offset+=len(vb);entry['ioff']=offset;ib=f.tobytes();chunks.append(ib);offset+=len(ib)
  entry['key']=len([g for g in cache.values() if g is not None]);cache[p.name]=entry
  return entry
 def walk(p,parent,chain,depth=0):
  if depth>6:raise ValueError('Assembly cycle')
  if p.suffix.lower()=='.sldprt':
   g=geom(p)
   if g:
    # Tessellation coordinates are mm; stored translations are metres.
    world=basis@parent;world[:3,3]*=1000
    center=(np.array(g['bbox'][0])+g['bbox'][1])/2
    anchor=(world@np.r_[center,1])[:3]
    instances.append(dict(mesh=g['key'],name=p.stem,chain=chain,matrix=world.T.reshape(-1).tolist(),anchor=anchor.tolist(),servo=p.name.startswith('HD-1910'),shell=any(s in p.name for s in ('shell_','face_part_','jaw_soft_','soft_mouth_')),id=None))
   return
  r=sldkit.parse_file(str(p))
  cfg=next((c for c in r.document.configurations if c.name and c.name.value=='默认'),r.document.configurations[0])
  for c in cfg.components:
   if c.is_suppressed and c.is_suppressed.value:continue
   if c.is_hidden and c.is_hidden.value:continue
   name=PureWindowsPath(c.stored_path.value).name
   raw=c.raw_attributes.get('swTransform')
   if not raw:raise ValueError('Missing transform '+name)
   mat=np.array([float(x) for x in raw.split()]).reshape(4,4).T
   if not np.allclose(mat[3],[0,0,0,1]):raise ValueError('Unexpected matrix')
   assembly_audit.append(dict(parent=p.name,part=name,transform=mat.T.reshape(-1).tolist()))
   child=files.get(name.lower())
   if not child:
    issues.append(dict(part=name,reason='missing referenced part',parent=p.name));continue
   walk(child,parent@mat,chain+[p.stem],depth+1)
 walk(top,np.eye(4),[])
 servos=[i for i in instances if i['servo']]
 assert len(servos)==15,f'Expected 15 servos, got {len(servos)}'
 def group(prefix):return [i for i in servos if any(s.startswith(prefix) for s in i['chain'])]
 def assign(seq,ids):
  assert len(seq)==len(ids),(len(seq),ids)
  for inst,sid in zip(seq,ids): inst['id']=sid
 assign(sorted(group('01_'),key=lambda i:i['anchor'][1]),[10,20])
 assign(group('02_'),[21]);assign(group('11_'),[11])
 assign(sorted(group('04_'),key=lambda i:-i['anchor'][2]),[22,23]);assign(sorted(group('13_'),key=lambda i:-i['anchor'][2]),[12,13])
 assign(group('05_'),[24]);assign(group('14_'),[14])
 assign(sorted(group('07_'),key=lambda i:i['anchor'][2]),[30,31])
 assign(group('09_'),[32])
 # In the head assembly: servo's output axis is local Z. Forward-facing
 # axis drives roll; lateral axis drives the jaw. Distinguish by world axis.
 head=[i for i in group('10_') if i['id'] is None]
 assign(sorted(head,key=lambda i:abs(np.array(i['matrix']).reshape(4,4).T[0,2]),reverse=True),[33,34])
 assert {i['id'] for i in servos}==set(NAMES)
 # Normalize so sole floor is z=0 and hip yaw midpoint is x=y=0.
 centers=[i['anchor'] for i in servos if i['id'] in (10,20)]
 shift=np.array([(centers[0][0]+centers[1][0])/2,(centers[0][1]+centers[1][1])/2,0.])
 bounds=[]
 for inst in instances:
  g=next(g for g in cache.values() if g and g['key']==inst['mesh']);lo,hi=np.array(g['bbox'])
  corners=np.array([[x,y,z,1] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
  world=np.array(inst['matrix']).reshape(4,4).T;bounds.extend((corners@world.T)[:,:3])
 shift[2]=np.min(bounds,axis=0)[2]
 for i in instances:
  m=np.array(i['matrix']).reshape(4,4).T;m[:3,3]-=shift;i['matrix']=m.T.reshape(-1).tolist();i['anchor']=(np.array(i['anchor'])-shift).tolist()
  if i['servo']:
   sid=i['id'];i.update(label=NAMES[sid][0],joint=NAMES[sid][1],description=DETAILS[sid],group='left' if 20<=sid<=24 else 'right' if 10<=sid<=14 else 'head')
 model=dict(source='fanhao375/microduck-replica-cad v2.1 · SolidWorks-FT2026-09-25',source_url='https://github.com/fanhao375/microduck-replica-cad/releases/tag/v2.1',assembly=top.name,assembly_sha256=hashlib.sha256(top.read_bytes()).hexdigest(),units='mm',axes={'forward':'+X','left':'+Y','up':'+Z'},instances=instances,meshes=sorted([v for v in cache.values() if v],key=lambda g:g['key']),issues=issues,notes=['Native saved display meshes; visualization, not manufacturing geometry.','Native assembly pose retained; not a servo zero-calibration pose.','Names and IDs follow the project runtime mapping; ID assignment is an annotation, not read from a motor.'])
 (a.out/'model.json').write_text(json.dumps(model,ensure_ascii=False,separators=(',',':')))
 (a.out/'meshes.bin').write_bytes(b''.join(chunks))
 (a.out/'audit.json').write_text(json.dumps(dict(issues=issues,transforms=assembly_audit,servos=[dict(id=i['id'],label=i['label'],anchor=i['anchor'],chain=i['chain']) for i in servos]),ensure_ascii=False,indent=2))
 print('DONE',len(instances),'instances',len(cache),'meshes',len(servos),'servos',offset,'bytes',issues)
 for i in sorted(servos,key=lambda s:s['id']):print(i['id'],i['label'],np.round(i['anchor'],1),i['chain'][-1])

if __name__=='__main__':main()
