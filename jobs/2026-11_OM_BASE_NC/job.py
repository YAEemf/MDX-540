"""Read-only CAD/CAM preparation. Never connects to the machine or emits RML.

Run from any directory: python jobs/2026-11_OM_BASE_NC/job.py
STEP changes are detected by hashes; rerun after corrected inputs are supplied.
"""
from pathlib import Path
import sys, json, hashlib, math
import csv
from collections import Counter
import cadquery as cq
import numpy as np
import trimesh
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely import affinity
import openpyxl

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'cam_output'))
import plan_tools as pt

CENTERS = [(100., 160.), (293., 160.)]
TOP_Y = 7.

def transform(shape, center=(0., 0.)):
    # Original thickness axis +Y -> machine +Z; revised top face Y=7 -> Z=0.
    return shape.rotate((0, 0, 0), (1, 0, 0), 90).translate((center[0], center[1], -TOP_Y))

def bounds(s):
    b = s.BoundingBox()
    return [b.xmin, b.ymin, b.zmin, b.xmax, b.ymax, b.zmax]

def section(mesh, z):
    cut = mesh.section(plane_origin=(0, 0, z), plane_normal=(0, 0, 1))
    result = Polygon()
    if cut is not None:
        for ring in cut.discrete:
            if len(ring) > 3:
                result = result.symmetric_difference(Polygon(ring[:, :2]).buffer(0))
    return result.simplify(.01, preserve_topology=True)

def main():
    paths = [ROOT/'model/393_320_10.step', ROOT/'model/OM_BASE_NC.step', ROOT/'ツール在庫.xlsx']
    stock, original = [cq.importers.importStep(str(p)).val() for p in paths[:2]]
    shape = transform(original)
    exact_bounds = bounds(shape)
    if abs(original.BoundingBox().ymax-TOP_Y) > 1e-6 or abs(original.BoundingBox().ylen-10) > 1e-6:
        raise ValueError('Input orientation/thickness changed: review setup transforms before regenerating')
    if abs(stock.BoundingBox().xlen-393) > 1e-6 or abs(stock.BoundingBox().ylen-320) > 1e-6 or abs(stock.BoundingBox().zlen-10) > 1e-6:
        raise ValueError('Stock changed: review nesting before regenerating')
    verts, faces = shape.tessellate(.01, .05)
    mesh = trimesh.Trimesh(vertices=[v.toTuple() for v in verts], faces=faces, process=True)
    planes = sorted({round(f.Center().z, 6) for f in shape.Faces()
                     if f.geomType() == 'PLANE' and abs(f.normalAt().z) > .999})
    probes = sorted(set([.001+min(planes), -.001] +
                        [(a+b)/2 for a,b in zip(planes, planes[1:])]))
    sections = {str(z): section(mesh, z) for z in probes}
    projection = unary_union(list(sections.values()))
    # Include all tessellation vertex heights to capture radial features between planar levels.
    projection = projection.union(unary_union([section(mesh, z) for z in np.linspace(min(planes)+.001, -.001, 12)]))
    placed = [affinity.translate(projection, *c) for c in CENTERS]
    cylinders = Counter()
    sideways = []
    for f in shape.Faces():
        if f.geomType() != 'CYLINDER':
            continue
        cyl = f._geomAdaptor().Cylinder()
        direction = cyl.Axis().Direction()
        cylinders[(round(cyl.Radius(), 5), round(abs(direction.Z()), 3))] += 1
        if abs(direction.Z()) < .01:
            sideways.append({'radius': cyl.Radius(), 'bounds': bounds(f), 'area': f.Area()})
    inventory = []
    for sheet in openpyxl.load_workbook(paths[2], data_only=True):
        for row, cells in enumerate(sheet.iter_rows(values_only=True), 1):
            if any(v is not None for v in cells):
                inventory.append({'sheet': sheet.title, 'row': row, 'values': list(cells)})
    # Existing conservative planning conditions, NOT validated A6063 cutting data.
    tools = [dict(t) for t in pt.TOOLS]
    tools += [dict(tools[-1], name='MHR230 1X14 (D1 conditional)', tested=False),
              dict(tools[1], name='CRNBEM-1-20-4 (D2 conditional)', reach=19., tested=False)]
    comparisons = pt.compare(lambda z: section(mesh, z), zb=sorted(planes, reverse=True),
                             tools=tools, show=False)
    # Compare reach per height separately: plan_tools is only a layered 2.5D estimator.
    reach = {}
    for z, part in sections.items():
        filled = pt._filled(part)
        holes = filled.difference(part)
        reach[z] = {t['name']: round(pt._close(filled, t['D']/2).difference(filled).area +
                       holes.difference(pt._open(holes, t['D']/2)).area, 4) for t in tools}
    data = {
        'status': 'GEOMETRY_REVIEW_NO_EXECUTABLE_PATH',
        'input_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        'material': 'A6063 (user specified)', 'stock_bounds_original': bounds(stock),
        'approved_tool_proposal': ['ATC1 D6 flat', 'ATC2 AC40-TLNE2010-14 D1 square', 'ATC3 PRB2.0-21 D2 ball'],
        'part_bounds_machine_centered': exact_bounds, 'z_planar_levels': planes,
        'centers_xy': CENTERS, 'transform': 'Xm=x+Cx; Ym=-z+Cy; Zm=y-7 (stock top zero)',
        'required_thickness': max(planes)-min(planes), 'available_thickness': stock.BoundingBox().zlen,
        'section_areas': {z: g.area for z,g in sections.items()},
        'projected_part_gap': placed[0].distance(placed[1]),
        'projected_bounds': [list(g.bounds) for g in placed],
        'D6_cutting_sweep_bounds': [list(g.buffer(6).bounds) for g in placed],
        'cylinders_radius_abs_axisZ_count': [[*k,v] for k,v in sorted(cylinders.items())],
        'sideways_cylinder_faces': sideways,
        'inventory_raw': inventory,
        'tool_conditions_basis': 'Existing plan_tools conservative steel estimates; conditional tools require measurement. Not A6063 validation.',
        'tool_comparisons_one_part_estimate_only': comparisons,
        'unreachable_area_mm2_by_section': reach,
        'blockers': ['Two-sided curved surfaces require Fusion 3D CAM, not existing axisymmetric cam.drop()',
                     'Tolerance and tool/setup measurements not supplied'],
    }
    top, lower = section(mesh, -.001), section(mesh, min(planes)+.001)
    data['top_roof_over_bottom_void_area_mm2_sampled'] = top.difference(lower).area
    # Exact BRep circular wires: no inference from cylinder-face counts.
    circles = []
    wires = cq.Workplane('XZ', origin=(0, TOP_Y-.01, 0)).add(original).section().val().Wires()
    for wire in wires:
        edges = wire.Edges()
        if len(edges) == 1 and edges[0].geomType() == 'CIRCLE':
            b = wire.BoundingBox()
            circles.append({'original_x': wire.Center().x, 'original_z': wire.Center().z,
                            'diameter': (b.xlen+b.zlen)/2})
    data['through_holes_per_part'] = circles
    data['setups'] = [
        {'name': 'A_bottom_up', 'map': 'X=x+Cx; Y=z+160; Z=-y-3',
         'work': 'Bottom reliefs: rough D6, rest D1, finish R3 surfaces with D2 ball; keep perimeter/frame attached'},
        {'name': 'B_top_up', 'map': 'X=x+Cx; Y=-z+160; Z=y-7',
         'work': 'Through holes, outer profile rough to Z-9, wall finish, final profile to Z-10.4 with tabs at Z-9.6'},
    ]
    data['flip_mapping'] = 'Rotate complete blank about its X midline: (X,Y,Z)->(X,320-Y,-10-Z); re-probe each setup'
    data['registration_proposal'] = {'method': 'Two measured fixture/dowel references in scrap bands; positions provisional',
                                     'A_xy': [[40,40],[353,40]], 'B_xy': [[40,280],[353,280]],
                                     'note': 'Pin size, fit and fixture method must be measured; do not machine nominal holes from this proposal.'}
    data['simulation_status'] = 'NOT RUN: no cutting path has been generated; layout and accessibility analysis only'
    with (HERE/'holes.csv').open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(['part','diameter_mm','A_X','A_Y','B_X','B_Y','top_Z','bottom_Z'])
        for i,(cx,cy) in enumerate(CENTERS,1):
            for hole in circles:
                x,z = hole['original_x'],hole['original_z']
                writer.writerow([i,round(hole['diameter'],5),round(cx+x,5),round(cy+z,5),round(cx+x,5),round(cy-z,5),0,-10])
    (HERE/'preparation.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    assembly = cq.Assembly(name='REFERENCE_ONLY_layout')
    assembly.add(stock.translate((196.5,160.,-10.)), name='stock_393x320x10', color=cq.Color(.7,.7,.7,.3))
    for i,c in enumerate(CENTERS, 1):
        assembly.add(transform(original,c), name=f'part_{i}_10mm', color=cq.Color(.1,.5,.9))
    assembly.save(str(HERE/'layout_reference.step'))
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="-15 -25 425 370">',
           '<rect x="0" y="0" width="393" height="320" fill="#eee" stroke="black"/>',
           '<text x="0" y="-10" font-size="7">REFERENCE: 2 x 10mm parts / 393 x 320 x 10 stock</text>',
           '<g transform="translate(0,320) scale(1,-1)">']
    for g in placed:
        svg.append(g.svg(scale_factor=.3, fill_color='#2c90d9'))
    svg += ['</g>', '<text x="5" y="335" font-size="6">Centers (100,160), (293,160); units mm. Not a toolpath or clamp approval.</text>', '</svg>']
    (HERE/'layout_reference.svg').write_text('\n'.join(svg), encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    figlayout, ax = plt.subplots(figsize=(11,9))
    ax.add_patch(Rectangle((0,0),393,320,facecolor='#eeeeee',edgecolor='black'))
    for i,g in enumerate(placed,1):
        for poly in pt._polys(g):
            ax.fill(*poly.exterior.xy, color='#87bfea')
            for hole in poly.interiors: ax.fill(*hole.xy, color='white')
        for poly in pt._polys(g.buffer(6)):
            ax.plot(*poly.exterior.xy, color='red', linestyle='--', linewidth=.7)
        ax.text(*CENTERS[i-1],str(i),ha='center')
    for y in (0,290):
        ax.add_patch(Rectangle((0,y),393,30,facecolor='green',alpha=.15))
    ax.set(xlim=(-10,403),ylim=(-10,330),xlabel='X / mm',ylabel='Y / mm',title='2-part layout; red = conservative D6 cutting envelope; green = proposed clamp bands')
    ax.set_aspect('equal'); ax.grid(alpha=.2)
    figlayout.savefig(HERE/'layout_reference.png',dpi=150); plt.close(figlayout)
    fig, axs = plt.subplots(1, len(sections), figsize=(6*len(sections),6))
    for ax,(z,g) in zip(np.atleast_1d(axs), sections.items()):
        for p in pt._polys(g):
            ax.plot(*p.exterior.xy)
            for hole in p.interiors: ax.plot(*hole.xy)
        ax.set_aspect('equal'); ax.set_title(f'Z={float(z):.3f} mm'); ax.grid()
    fig.savefig(HERE/'sections.png', dpi=130); plt.close(fig)
    print(json.dumps({k:data[k] for k in ['status','part_bounds_machine_centered','z_planar_levels','section_areas','projected_part_gap','D6_cutting_sweep_bounds']}, indent=2))
    for r in comparisons[:8]:
        print(r)

if __name__ == '__main__':
    main()
