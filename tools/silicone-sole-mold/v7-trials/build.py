"""Generate two prefill/compression molds for Microduck's stock silicone boot geometry.
Run with uv run --script build.py --help for required paths. Outputs in mm. CC BY-NC-SA 4.0.
"""
# /// script
# dependencies = ["numpy", "trimesh", "manifold3d", "mapbox-earcut", "scipy", "shapely", "matplotlib", "networkx", "lxml"]
# ///
from pathlib import Path
import hashlib
import json
import argparse

import manifold3d as mf
import mapbox_earcut as earcut
import numpy as np
import trimesh as tm
from shapely.geometry import Polygon, LineString

OUT = Path(__file__).resolve().parent
SOURCE = OUT.parent / 'microduck-replica/print/打印件'
M = mf.Manifold
WIDTH, LENGTH = 64, 76
BASE, LID = 18, 6
PIN_HEIGHT, PIN_ROOT, PIN_TIP = 3.6, 2.7, 2.3
SOCKET_RADIUS, SOCKET_DEPTH = 2.95, 4.3
VENT_RADIUS = 1.2
VARIANTS = {
    'A': (1.5, 1.5, 'uniform_1p5'),
    'B': (2.0, 2.0, 'uniform_2p0'),
    'C': (1.0, 2.0, 'body_1p0_rim_2p0'),
    'D': (1.5, 2.5, 'body_1p5_rim_2p5'),
}
VARIANT = 'A'
SIDE_INSET, RIM_INSET, PROFILE = VARIANTS[VARIANT]


def label_cut(text):
    # Engraved on the outside back face, clear of pins and vent mouths.
    from matplotlib.textpath import TextPath
    from shapely.geometry import Polygon
    shape = Polygon()
    for vertices in TextPath((0, 0), text, size=4.5).to_polygons():
        shape = shape.symmetric_difference(Polygon(vertices))
    polygons=list(shape.geoms) if hasattr(shape, 'geoms') else [shape]
    pieces=[solid(tm.creation.extrude_polygon(p, height=.7, engine='earcut')) for p in polygons]
    body=pieces[0]
    for piece in pieces[1:]: body+=piece
    return body.translate([-7, 30, LID-.5])


def solid(mesh):
    result = M(mf.Mesh64(np.array(mesh.vertices), np.array(mesh.faces, dtype=np.uint64)))
    assert result.status() == mf.Error.NoError and not result.is_empty()
    return result


def trimesh(solid):
    raw = solid.to_mesh64()
    return tm.Trimesh(np.array(raw.vert_properties)[:, :3], np.array(raw.tri_verts))


def fill_open_shell(shell):
    # Removing the planar rim leaves a single boundary cycle on each skin.
    edges, counts = np.unique(shell.edges_sorted, axis=0, return_counts=True)
    cycle = boundary_loop(edges[counts == 1])
    xy = shell.vertices[cycle, :2]
    assert Polygon(xy).is_valid
    triangles = earcut.triangulate_float64(xy, np.array([len(xy)], dtype=np.uint32))
    cap = cycle[triangles.reshape(-1, 3)]
    mesh = tm.Trimesh(shell.vertices, np.vstack([shell.faces, cap]))
    mesh.fix_normals()
    assert mesh.is_volume and mesh.is_watertight
    return solid(mesh), Polygon(xy)


def boundary_loop(edges):
    neighbors = {}
    for i, j in edges:
        neighbors.setdefault(i, []).append(j)
        neighbors.setdefault(j, []).append(i)
    assert all(len(pair) == 2 for pair in neighbors.values())
    cycle = [next(iter(neighbors))]
    last = None
    while True:
        i = cycle[-1]
        j = next(v for v in neighbors[i] if v != last)
        if j == cycle[0]:
            break
        last = i
        cycle.append(j)
    assert len(cycle) == len(neighbors)
    return np.array(cycle)


def plate(width, height, z):
    return M.cube([width, LENGTH, height]).translate([-width/2, -LENGTH/2, z])


def cyl(x, y, z, height, radius, end=None):
    return M.cylinder(height, radius, radius if end is None else end, 64).translate([x, y, z])


def printable(body, name, flip=False):
    oriented = body.rotate((180, 0, 0)) if flip else body
    mesh = trimesh(oriented)
    mesh.apply_translation(-mesh.bounds[0])
    # Remove zero-volume coplanar remnants of the band/core union after
    # conversion to STL precision. Keep and verify the one physical solid.
    mesh=tm.Trimesh(mesh.vertices.astype(np.float32).astype(np.float64),mesh.faces)
    components=sorted(mesh.split(only_watertight=False),key=lambda m:abs(m.volume),reverse=True)
    assert sum(abs(m.volume) for m in components[1:])<1e-6
    mesh=components[0]
    assert mesh.is_volume and abs(mesh.volume-body.volume())<.05
    path = OUT / name
    mesh.export(path)
    mesh.export(path.with_suffix('.3mf'))
    mesh.copy().apply_scale(.1).export(path.with_name(path.stem+'_IMPORT_AS_CM.stl'))
    readback = tm.load_mesh(path)
    assert readback.is_volume and readback.is_watertight and len(readback.split()) == 1
    assert abs(readback.volume-body.volume()) < 0.05
    return readback


def build(side, cn):
    path = SOURCE / f'sole_{side}_{cn}脚底.stl'
    boot = tm.load_mesh(path).apply_scale(1000)
    assert boot.is_volume
    boot.apply_translation([-boot.bounds.mean(axis=0)[0], -boot.bounds.mean(axis=0)[1], -boot.bounds[1, 2]])
    rim = (abs(boot.triangles[:, :, 2]) < 1e-7).all(axis=1)
    skins = boot.submesh([~rim], append=True).split(only_watertight=False, repair=False)
    inner, outer = sorted(skins, key=lambda m: m.extents[0])
    core, inner_loop = fill_open_shell(inner)
    envelope, outer_loop = fill_open_shell(outer)
    target = solid(boot)
    restored = envelope - core
    assert (restored - target).volume() + (target - restored).volume() < .002
    # Inset the full-height sidewalls. Blend only where the wall rolls
    # into the bottom, referenced to the original tilted floor plane.
    old_core=core
    original_mesh=trimesh(core)
    bounds=original_mesh.bounds
    center=bounds.mean(axis=0)
    extents=bounds[1]-bounds[0]
    factors=[1-2*RIM_INSET/extents[0],1-2*RIM_INSET/extents[1],1]
    pts=original_mesh.vertices
    sample=(abs(pts[:,0]-center[0])<8)&(abs(pts[:,1]-center[1])<10)&(pts[:,2]<-3)
    fit=np.column_stack([pts[sample,:2],np.ones(sample.sum())])
    coef=np.linalg.lstsq(fit,pts[sample,2],rcond=None)[0]
    refined=trimesh(core.refine_to_length(.45))
    v=refined.vertices.copy()
    height=v[:,2]-np.column_stack([v[:,:2],np.ones(len(v))])@coef
    weight=np.clip((height-.5)/2,0,1)
    weight=weight**2*(3-2*weight)
    # Full extra rim inset over the top 1 mm; smooth transition from 4 to 1 mm below rim.
    rim_weight=np.clip((v[:,2]+4)/3,0,1)
    rim_weight=rim_weight**2*(3-2*rim_weight)
    inset=SIDE_INSET+(RIM_INSET-SIDE_INSET)*rim_weight
    for axis in (0,1):
        v[:,axis]=center[axis]+(v[:,axis]-center[axis])*(1-weight*2*inset/extents[axis])
    refined.vertices=v
    core=solid(refined)
    from shapely import affinity
    inner_loop=affinity.scale(inner_loop,xfact=factors[0],yfact=factors[1],origin=tuple(center[:2]))
    target=envelope-core
    assert (core-old_core).volume()<.001
    components=sorted(target.decompose(),key=lambda p:abs(p.volume()),reverse=True)
    assert sum(abs(p.volume()) for p in components[1:])<1e-8
    target=components[0]
    floor_deltas=[]
    for x in np.arange(-10,10.1,1):
        for y in np.arange(-14,14.1,1):
            rays=[body.ray_cast([x,y,-16],[x,y,-.01]) for body in (old_core,core)]
            heights=[min(hit.position[2] for hit in hits) for hits in rays]
            floor_deltas.append(abs(heights[1]-heights[0]))
    assert max(floor_deltas)<.001
    base = plate(WIDTH, BASE, -BASE) - envelope
    lid = plate(WIDTH+4, LID, 0) + core
    # The third pin is offset. The opposite side has a different offset.
    keys = [(-26, -32), (26, -32), (-22 if side == 'left' else -12, 32)]
    for x, y in keys:
        pin = cyl(x, y, -PIN_HEIGHT, PIN_HEIGHT, PIN_TIP, PIN_ROOT)
        lid += pin
        base -= cyl(x, y, -SOCKET_DEPTH, SOCKET_DEPTH+.02, SOCKET_RADIUS)
    lid -= label_cut(VARIANT + ("-L" if side=="left" else "-R"))
    # Mid-edge and corner vent bores stay fully inside the silicone rim.
    vents = []
    vent_clearances = []
    for i, (dx, dy) in enumerate([(1, 0), (-1, 0), (0, 1), (0, -1), (1,1.4), (-1,1.4), (1,-1.4), (-1,-1.4)]):
        ray = LineString([(0, 0), (100*dx, 100*dy)])
        ends = [poly.boundary.intersection(ray) for poly in (outer_loop, inner_loop)]
        a, b = (np.array(p.coords[0]) for p in ends)
        x, y = (a+b)/2
        vents.append([x, y])
        radius=VENT_RADIUS if i<4 else 1.0
        from shapely.geometry import Point
        ring=outer_loop-inner_loop
        clearance=ring.boundary.distance(Point(x,y))-radius
        assert ring.contains(Point(x,y)) and clearance>0
        vent_clearances.append(clearance)
        lid -= cyl(x, y, -.05, LID+.1, radius)
        lid -= cyl(x, y, LID-1, 1.1, radius, radius+.6)
        assert not lid.ray_cast([x,y,-.01],[x,y,LID+.1])
        assert (target^cyl(x,y,-.03,.02,.1)).volume()>1e-5
    # Confirm that subtracting the tools from the full, unported lower
    # block recovers exactly the stock boot after the registration sockets are masked.
    cast = (envelope - base) - lid
    err = (cast - target).volume() + (target - cast).volume()
    assert err < .01 and abs((base ^ lid).volume()) < .001
    for dz in np.arange(.25, 14, .25):
        assert (base ^ lid.translate([0, 0, dz])).volume() < .001
    lower = printable(base, f'{side}_01_cavity.stl')
    upper = printable(lid, f'{side}_02_core.stl', True)
    info = dict(source=path.name, variant=VARIANT, profile=PROFILE,
                side_inset_mm=SIDE_INSET, rim_inset_mm=RIM_INSET,
                scope="full sidewall with optional tighter rim; bottom blend retained",
                rim_blend_z_range_mm=[-4,-1],
                core_max_width_length_mm=trimesh(core).extents[:2].tolist(),
                approximate_opening_stretch_to_stock_foot_percent=((extents[:2]/(extents[:2]-2*RIM_INSET)-1)*100).tolist(),
                minimum_vent_edge_clearance_mm=min(vent_clearances),
                floor_blend_height_range_mm=[.5,2.5],
                sampled_central_floor_max_change_mm=max(floor_deltas), core_xy_scale=factors[:2],
                original_core_width_length_mm=extents[:2].tolist(),
                new_opening_width_length_mm=(extents[:2]-2*RIM_INSET).tolist(),
                added_silicone_mL=(old_core.volume()-core.volume())/1000,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                boot_mm=boot.extents.tolist(), silicone_net_mL=target.volume()/1000,
                cast_difference_mm3=err, vents_xy_mm=vents,
                vents_per_foot=8, vent_diameters_mm=[2.4]*4+[2.0]*4, outlet_flare_depth_mm=1,
                verified_print_extents_mm=[lower.extents.tolist(), upper.extents.tolist()])
    return info, (lower, upper, trimesh(target))


def render_part(mesh, color):
    # Orthographic raster with a depth buffer so concave seats hide rear faces.
    from matplotlib.colors import to_rgb
    view = np.array([.58, -.73, .57])
    view /= np.linalg.norm(view)
    across = np.cross([0, 0, 1], view)
    across /= np.linalg.norm(across)
    up = np.cross(view, across)
    pts = mesh.vertices @ np.stack([across, -up, view], axis=1)
    size = 720
    pts[:, :2] -= (pts[:, :2].max(0) + pts[:, :2].min(0))/2
    pts[:, :2] *= (size-35) / np.ptp(pts[:, :2], axis=0).max()
    pts[:, :2] += size/2
    canvas = np.full((size, size, 3), [246, 248, 251], dtype='uint8')
    depth = np.full((size, size), -np.inf)
    for face, normal in zip(mesh.faces, mesh.face_normals):
        if normal @ view <= 0:
            continue
        tri = pts[face]
        lo = np.ceil(tri[:, :2].min(0)).astype(int)
        hi = np.floor(tri[:, :2].max(0)).astype(int)
        x0, y0 = lo
        x1, y1 = hi+1
        y, x = np.ogrid[y0:y1, x0:x1]
        a, b, c = tri
        u, v = b-a, c-a
        det = u[0]*v[1] - u[1]*v[0]
        if abs(det) < 1e-9:
            continue
        w = ((x-a[0])*v[1] - (y-a[1])*v[0])/det
        t = ((y-a[1])*u[0] - (x-a[0])*u[1])/det
        z = a[2] + w*u[2] + t*v[2]
        region = depth[y0:y1, x0:x1]
        visible = (w >= -1e-8) & (t >= -1e-8) & (w+t <= 1.00000001) & (z > region)
        region[visible] = z[visible]
        shade = .72 + .32*(normal @ [-.4, -.37, .83])
        canvas[y0:y1, x0:x1][visible] = np.clip(255*shade*np.array(to_rgb(color)), 0, 255)
    return canvas


def preview(parts):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.5), facecolor='#f6f8fb')
    fig.suptitle('MICRODUCK    /    Silicone sole mold', x=.055, y=.96, ha='left',
                 fontsize=20, fontweight='bold', color='#183248')
    for ax, mesh, color, title in zip(axes, parts, ['#8598ac', '#72afc5', '#efaa68'],
            ['01   Cavity', '02   Core and alignment pins', '03   Silicone boot']):
        ax.imshow(render_part(mesh, color))
        ax.axis('off')
        ax.set_title(title, fontsize=12, color='#183248', y=.04)
    fig.text(.055,.04,'Left foot shown    /    Both halves in print orientation    /    Units: mm',
             fontsize=10, color='#4e6477')
    fig.subplots_adjust(left=.02, right=.98, wspace=.03, bottom=.13, top=.85)
    fig.savefig(OUT/'preview.png', dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,required=True,help='Directory with original sole_left_左脚底.stl and sole_right_右脚底.stl in metres')
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--variant',choices=VARIANTS,required=True)
    args=parser.parse_args()
    SOURCE=args.source_dir.resolve();OUT=args.output_dir.resolve();OUT.mkdir(parents=True,exist_ok=True)
    VARIANT=args.variant;SIDE_INSET,RIM_INSET,PROFILE=VARIANTS[VARIANT]
    results={}
    for side,cn in [('left','左'),('right','右')]:
        results[side],parts=build(side,cn)
        print(VARIANT,side,results[side]['silicone_net_mL'],flush=True)
    (OUT/'checks.json').write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n')
