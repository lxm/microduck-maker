from pathlib import Path
import argparse,json
import numpy as np,trimesh as tm
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from build import solid,trimesh,M,render_part,cyl
parser=argparse.ArgumentParser()
parser.add_argument('--output-dir',type=Path,required=True)
parser.add_argument('--reference-dir',type=Path,required=True)
args=parser.parse_args()
ROOT=args.output_dir.resolve();OLD=args.reference_dir.resolve()
checks=json.loads((ROOT/'checks.json').read_text())
report={}
assemblies=[]
for side in ['left','right']:
    for part in ['01_cavity','02_core']:
        name=side+'_'+part
        a=tm.load_mesh(ROOT/(name+'.stl'))
        b=tm.load_mesh(ROOT/(name+'_IMPORT_AS_CM.stl')).apply_scale(10)
        path=ROOT/(name+'.3mf')
        with ZipFile(path) as z: assert ET.fromstring(z.read('3D/3dmodel.model')).get('unit')=='millimeter'
        c=tm.load_scene(path).to_geometry()
        for mesh in [a,b,c]:
            assert mesh.is_volume and len(mesh.split())==1
            assert np.allclose(mesh.bounds,a.bounds,atol=1e-4)
            assert np.isclose(mesh.volume,a.volume,rtol=1e-6)
    base=solid(tm.load_mesh(ROOT/(side+'_01_cavity.stl')).apply_translation([-32,-38,-18]))
    lid=solid(tm.load_mesh(ROOT/(side+'_02_core.stl')).apply_translation([-34,-38,-6])).rotate([180,0,0])
    for (x,y),diam in zip(checks[side]['vents_xy_mm'],checks[side]['vent_diameters_mm']):
        assert (lid^cyl(x,y,.001,5.998,diam/2-.002)).volume()<.001
    old_base=solid(tm.load_mesh(OLD/(side+'_01_cavity.stl')).apply_translation([-32,-38,-18]))
    old_lid=solid(tm.load_mesh(OLD/(side+'_02_core.stl')).apply_translation([-34,-38,-6])).rotate([180,0,0])
    base_error=(base-old_base).volume()+(old_base-base).volume()
    assert base_error<.001
    protected=M.cube([20,28,17]).translate([-10,-14,-23])
    lower_error=((old_lid^protected)-(lid^protected)).volume()+((lid^protected)-(old_lid^protected)).volume()
    assert lower_error<.01,lower_error
    assert (base^lid).volume()<.001
    for dz in np.arange(.25,14,.5): assert (base^lid.translate([0,0,dz])).volume()<.001
    space=M.cube([44,56,14]).translate([-22,-28,-14])
    cast=space-base-lid
    old_cast=space-old_base-old_lid
    added=cast-old_cast
    removed=old_cast-cast
    assert removed.volume()<.01
    assert (added^protected).volume()<.01
    report[side]={'lower_mold_difference_from_V1_mm3':base_error,
                  'central_floor_geometry_difference_mm3':lower_error,
                  'silicone_removed_from_V1_mm3':removed.volume(),
                  'added_silicone_mm3':added.volume(),
                  'assembled_collision_mm3':(base^lid).volume()}
    assemblies.append((base,lid,cast,added,old_lid))
# Wrong left/right upper-to-lower pairing is checked separately.
wrong=(assemblies[0][0]^assemblies[1][1]).volume()
assert wrong>5
report['wrong_left_lower_right_upper_overlap_mm3']=wrong
(ROOT/'assembly-checks.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
font_path=Path('/System/Library/Fonts/STHeiti Medium.ttc')
font=FontProperties(fname=str(font_path)) if font_path.exists() else FontProperties()
fig,axes=plt.subplots(1,2,figsize=(11,5),facecolor='#f6f8fb')
base,lid,cast,added,old_lid=assemblies[0]
for ax in axes:
    ax.set_facecolor('#f6f8fb')
    for body,color in [(base,'#8eacb5'),(lid,'#74b9ca'),(cast,'#f2b76f'),(added,'#eb743b')]:
        section=trimesh(body).section(plane_origin=[0,0,0],plane_normal=[0,1,0])
        if section is None: continue
        for curve in section.discrete:
            ax.fill(curve[:,0],curve[:,2],color=color,edgecolor=color,linewidth=.2)
    ax.set_aspect('equal')
    ax.set_xlabel('X / mm')
    ax.set_ylabel('Z / mm')
axes[0].set(xlim=(-23,23),ylim=(-15,3))
axes[1].set(xlim=(16,21.5),ylim=(-11,1))
axes[0].set_title('原版外形 · 整圈内侧壁加厚',fontproperties=font,fontsize=15)
axes[1].set_title('侧壁放大 · 深橙色为新增硅胶',fontproperties=font,fontsize=15)
fig.suptitle(f"试验 {checks['left']['variant']} / 侧壁 {checks['left']['side_inset_mm']} mm · 上沿 {checks['left']['rim_inset_mm']} mm",fontproperties=font,fontsize=19)
fig.text(.06,.03,'深橙色为新增硅胶。外形、原纹理与中央底面保留；排气与防脱待实测。',fontproperties=font,fontsize=12)
fig.subplots_adjust(top=.84,bottom=.16,wspace=.3)
fig.savefig(ROOT/'inner-wall.png',dpi=170)
