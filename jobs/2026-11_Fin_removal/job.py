"""Fin-only CAM preparation; offline previews only, never emits RML or sends data.

python jobs/2026-11_Fin_removal/job.py
All preview coordinates: stock front-left XY, original fin tips Z0, plate face Z-75.
Final 0.2 mm is deliberately NOT included until actual plate height is measured.
"""
from pathlib import Path
import csv
import hashlib
import itertools
import json
import math
import sys

import cadquery as cq
import numpy as np
import openpyxl
from shapely.geometry import LineString, box
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'cam_output'))
import cam
import sim

ALLOWANCE = 0.2
OVERTRAVEL = 5.0
LOCAL_CLEARANCE = 1.0
RPM = 5040


def bounds(shape):
    b = shape.BoundingBox()
    return [b.xmin, b.ymin, b.zmin, b.xmax, b.ymax, b.zmax]


def machine_shape(shape):
    # Fin direction originally -Z; turn the entire billet over, base fully supported.
    return shape.rotate((0, 0, 0), (1, 0, 0), 180).translate((196.5, 160, -75))


def analyze():
    src = ROOT / 'model/393_320_10_Fin.step'
    target_src = ROOT / 'model/393_320_10.step'
    raw = cq.importers.importStep(str(src)).val()
    plate = cq.importers.importStep(str(target_src)).val()
    assert raw.isValid() and plate.isValid()
    assert np.allclose(bounds(raw), [-196.5, -160, -75, 196.5, 160, 10])
    assert np.allclose(bounds(plate), [-196.5, -160, 0, 196.5, 160, 10])
    assert plate.cut(raw).Volume() < 1e-5
    stock, target = machine_shape(raw), machine_shape(plate)
    fins = []
    for f in stock.Faces():
        if f.geomType() == 'PLANE' and f.normalAt().z > .999 and abs(f.Center().z) < 1e-6:
            x0, y0, _, x1, y1, _ = bounds(f)
            assert abs(x0) < 1e-6 and abs(x1 - 393) < 1e-6
            assert abs(y1 - y0 - 5) < 1e-6
            # STEP transform roundoff must not turn exact boundary grid cells into stock.
            fins.append([round(v, 6) for v in [x0, y0, x1, y1]])
    fins.sort(key=lambda b: b[1])
    assert len(fins) == 17
    footprint = unary_union([box(*b) for b in fins])
    removed = stock.cut(target)
    assert abs(removed.Volume() - footprint.area * 75) < 1e-4
    inv = []
    for sheet in openpyxl.load_workbook(ROOT / 'ツール在庫.xlsx', data_only=True):
        for i, row in enumerate(sheet.values, 1):
            if any(v is not None for v in row):
                inv.append(dict(sheet=sheet.title, row=i, values=list(row)))
    # Preserve original workbook text, including its existing mojibake. Never rewrite inventory.
    return stock, target, fins, footprint, removed, inv


def scan_offsets(fin, diameter, ae, centered=False):
    y0, y1 = fin[1], fin[3]
    if centered:
        assert diameter >= y1-y0
        return [(y0+y1)/2]
    n = math.ceil((y1-y0) / ae - 1e-9)
    # Clear from -Y to +Y; +X moves leave remaining material on the left (climb).
    return [y0-diameter/2 + (y1-y0)*i/n for i in range(1, n+1)]


def make_path(fins, *, name, ap, feed, ae=1., centered=False, bidirectional=False, start=0., bottom=-74.8):
    p = cam.Path(1, cam.rc(RPM), name)
    jobs = []
    x0, x1 = -OVERTRAVEL, 393+OVERTRAVEL
    p.rapid(x0, scan_offsets(fins[0], 6., ae, centered)[0], 5.)
    previous = start
    scan_number = 0
    for layer, z in enumerate(cam.levels(start, bottom, ap), 1):
        first = len(p.m)
        safe = previous + LOCAL_CLEARANCE
        for fin in fins:
            for y in scan_offsets(fin, 6., ae, centered):
                reverse = bidirectional and scan_number % 2 == 1
                entry, exit_x = (x1, x0) if reverse else (x0, x1)
                # Retract above ALL remaining fins before returning across the billet.
                x, yy, _ = p.pos
                p.rapid(x, yy, safe)
                p.rapid(entry, y, safe)
                # Pure Z descent is entirely OUTSIDE the material (2 mm cutter clearance).
                p.rapid(entry, y, z)
                p.feed(exit_x, y, z, feed)
                scan_number += 1
        x, y, _ = p.pos
        p.rapid(x, y, safe)
        jobs.append(dict(layer=layer, previous_top=previous, bottom=z,
                         first_move=first, last_move=len(p.m)-1))
        previous = z
    x, y, _ = p.pos
    p.rapid(x, y, 5.)
    return p, jobs


def verify_path(p, layers, fins, footprint, centered, ae):
    """Exact XY swept sets + every-segment clearance check on prismatic, levelled stock.

    This verifies the cutter envelope, not holder/fixture or cutting forces.
    """
    sweeps = []
    for fin in fins:
        for y in scan_offsets(fin, 6., ae, centered):
            sweeps.append(LineString([(-5, y), (398, y)]).buffer(3., quad_segs=64))
    swept = unary_union(sweeps)
    missed = footprint.difference(swept).area
    assert missed < 1e-6
    rapid_cut = 0
    max_ap = 0
    for layer in layers:
        previous_top, bottom = layer['previous_top'], layer['bottom']
        max_ap = max(max_ap, previous_top-bottom)
        for i in range(layer['first_move'], layer['last_move']+1):
            x, y, z, feed = p.m[i]
            px, py, pz, _ = p.m[i-1]
            if feed is None:
                vertical_outside = abs(x-px) < 1e-8 and abs(y-py) < 1e-8 and (x <= -5 or x >= 398)
                above_stock = min(z, pz) >= previous_top + .999999
                # The first retract may begin at the last cut height: X is outside stock.
                if not (vertical_outside or above_stock):
                    rapid_cut += 1
            else:
                assert abs(y-py) < 1e-8 and abs(z-pz) < 1e-8
                assert sorted([px, x]) == [-5, 398] and abs(z-bottom) < 1e-8
                assert z >= -75 + ALLOWANCE - 1e-7
    assert rapid_cut == 0
    return dict(uncovered_fin_area_mm2=missed, rapid_cut_segments=rapid_cut,
                maximum_layer_ap_mm=max_ap, cutter_sweep_bounds=list(swept.bounds),
                min_tip_z_mm=min(m[2] for m in p.m), plate_gouge_volume_mm3=0.,
                rough_residual_volume_mm3=footprint.area*(75+min(m[2] for m in p.m)),
                scope='Exact straight-line cutter envelopes; excludes holder, clamps, force, deflection, runout and datum errors')


def compare_tools(fins):
    """Compare every subset of dimensioned inventory candidates for THIS stock geometry.

    plan_tools.compare assumes empty pocket regions or profile cutting with fixed depth
    below ORIGINAL stock top. It would reject D6 for the 5 mm fin width and 75 mm depth.
    Here the cutter may overhang a fin, and depth is below CURRENT globally levelled top.
    Use explicit fin-facing raster lengths, not that inapplicable pocket model.
    """
    tools = [
        dict(name='D6 flat (xlsx row 4)', D=6., flat=True, ap=.2, ae=1., F=180., centered=True),
        dict(name='AC40 D1 square (row 10; dimensions from machine records)', D=1., flat=True, ap=.03, ae=.2, F=80., centered=False),
        dict(name='MHR230 D1 (row 3)', D=1., flat=True, ap=.03, ae=.2, F=80., centered=False),
        dict(name='PRB D2 ball (row 11; dimensions from machine records)', D=2., flat=False, ap=.1, ae=.2, F=150., centered=False),
        dict(name='CRNBEM D2 ball (row 7; dimensions from machine records)', D=2., flat=False, ap=.1, ae=.2, F=150., centered=False),
        dict(name='D3 ball (row 5)', D=3., flat=False, ap=.1, ae=.2, F=150., centered=False),
    ]
    rows = []
    rough_volume = 17*393*5*74.8
    for n in range(1, len(tools)+1):
        for subset in itertools.combinations(tools, n):
            # Even an ideal mixed-tool strategy cannot exceed the fastest constituent MRR
            # at these assumed conditions. Includes ball roughing + small flat finishing.
            max_mrr = max(t['ap']*(5 if t['centered'] else t['ae'])*t['F'] for t in subset)
            lower_bound = rough_volume/max_mrr
            flats = [t for t in subset if t['flat']]
            candidates = []
            for t in flats:
                passes = sum(len(scan_offsets(b, t['D'], t['ae'], t['centered'])) for b in fins)
                layers = math.ceil(74.8/t['ap']-1e-9)
                cut_minutes = passes*layers*403/t['F']
                candidates.append((cut_minutes, t, passes, layers))
            if candidates:
                minutes, t, passes, layers = min(candidates, key=lambda x: x[0])
                rows.append(dict(tools=[x['name'] for x in subset], used=t['name'],
                                 cutting_minutes_lower_bound=minutes, scans_per_layer=passes,
                                 ideal_any_mixed_strategy_lower_bound_minutes=lower_bound,
                                 comparison_scope='Flat-only path estimate, plus optimistic MRR bound for any mixed rough/rest strategy; mixed ball-rough paths not generated',
                                 layers=layers, inaccessible_mm3=0.,
                                 unused_tools=[x['name'] for x in subset if x is not t],
                                 status='Flat floor accessible; all conditions provisional'))
            else:
                # At a 0.2 mm raster pitch even the largest ball leaves a positive cusp.
                r = max(t['D'] for t in subset)/2
                cusp = r-math.sqrt(r*r-.1**2)
                rows.append(dict(tools=[x['name'] for x in subset], used=None,
                                 cutting_minutes_lower_bound=None, inaccessible_mm3=None,
                                 ideal_any_mixed_strategy_lower_bound_minutes=lower_bound,
                                 ball_floor_cusp_mm=cusp,
                                 status='Not selected: ball raster leaves scallops; no flat floor finishing tool'))
    return rows


def save_preview(path, layers):
    with (HERE/(path.name+'.csv')).open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['OFFLINE_PREVIEW_ONLY', 'X_mm', 'Y_mm', 'Z_mm', 'F_mm_min_or_RAPID'])
        for i, (x,y,z,feed) in enumerate(path.m):
            w.writerow([i, round(x,5), round(y,5), round(z,5), 'RAPID' if feed is None else feed])
    with (HERE/(path.name+'_layers.csv')).open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(layers[0]))
        w.writeheader(); w.writerows(layers)


def layer_simulation(fins, footprint, centered, ae, feed, ap, bidirectional=False):
    # One representative whole layer; translation in Z gives every roughing layer.
    # Exact swept checks above cover ALL layers, including transitions.
    sim.set_grid(half=210., res=.5)
    H = np.full((sim.N, sim.N), -99.)
    plate_mask = (abs(sim.GX) < 196.5) & (abs(sim.GY) < 160.)
    H[plate_mask] = -75.
    import shapely
    mask = shapely.contains_xy(footprint, sim.GX+196.5, sim.GY+160.)
    H[mask] = 0.
    p, _ = make_path(fins, name='representative_layer', ap=ap, feed=feed,
                     ae=ae, centered=centered, bidirectional=bidirectional, bottom=-ap)
    q = cam.Path(1, cam.rc(RPM), p.name)
    q.m = [(x-196.5, y-160., z, f) for x,y,z,f in p.m]
    out, rep = sim.simulate([(q, 6., False)], H=H)
    assert rep[0][3] == 0
    assert rep[0][1] <= ap + 1e-6
    assert np.all(out[mask] <= -ap+1e-6)
    assert np.all(out[plate_mask] >= -75-1e-6)
    return dict(grid_mm=.5, report=rep, fin_cells_left_above_layer=int((out[mask] > -ap+1e-6).sum()),
                note='Representative complete layer only; exact all-layer verification stored separately. No fixture/holder simulation.')


def main():
    stock, target, fins, footprint, removed, inventory = analyze()
    comparisons = compare_tools(fins)  # BEFORE paths, all 63 subsets
    scenarios = [
        dict(name='rough_D6_alternating_PREVIEW', ap=.2, feed=180., ae=1., centered=True, bidirectional=True),
        dict(name='rough_D6_centerline_PREVIEW', ap=.2, feed=180., ae=1., centered=True),
        dict(name='rough_D6_side_steps_PREVIEW', ap=.2, feed=250., ae=1., centered=False),
    ]
    output = []
    for spec in scenarios:
        path, layers = make_path(fins, **spec)
        checks = verify_path(path, layers, fins, footprint, spec['centered'], spec['ae'])
        simulation = layer_simulation(fins, footprint, spec['centered'], spec['ae'], spec['feed'], spec['ap'], spec.get('bidirectional',False))
        save_preview(path, layers)
        cutting = sum(403/spec['feed'] for m in path.m if m[3] is not None)
        minutes = cam.est_minutes(path)
        # Existing RML exporter throttles rapid segments below Z+1 to 20 mm/s.
        rml_estimate = cutting
        for a,b in zip(path.m, path.m[1:]):
            if b[3] is None:
                rml_estimate += math.dist(a[:3], b[:3])/(1200. if b[2] < 1 else 3600.)
        output.append(dict(**spec, layers=len(layers), move_count=len(path.m),
                           cam_est_minutes=minutes, exporter_speed_est_minutes=rml_estimate,
                           cutting_feed_minutes=cutting, cutting_feed_air_fraction=10/403,
                           checks=checks, representative_layer_simulation=simulation))
    trial_path, trial_layers = make_path(fins, name='trial_D6_ap0p05_PREVIEW', ap=.05,
                                        feed=180., centered=True, bidirectional=True, bottom=-.05)
    trial_checks = verify_path(trial_path, trial_layers, fins, footprint, True, 1.)
    save_preview(trial_path, trial_layers)
    inputs = [ROOT/'model/393_320_10_Fin.step', ROOT/'model/393_320_10.step', ROOT/'ツール在庫.xlsx']
    data = dict(status='OFFLINE_PREPARATION_ONLY_NO_RML', material='A6063',
                transform='X=x+196.5; Y=160-y; Z=-z-75; fins UP, original z=10 face DOWN',
                input_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
                stock_bounds=bounds(stock), target_bounds=bounds(target), fins_xy_bounds=fins,
                fin_count=len(fins), fin_width=5., fin_height=75., fin_pitch=17.0625,
                gap=12.0625, edge_clamp_lands_y=[[0,21],[299,320]],
                remove_volume_mm3=removed.Volume(), rough_allowance_mm=ALLOWANCE,
                final_finish_status='NOT GENERATED: measure base-face height, warp, zero uncertainty; then set shallow finish without cutting below protected plate',
                rpm=RPM, RC=cam.rc(RPM), inventory_raw=inventory,
                trial_preview=dict(file=trial_path.name+'.csv', checks=trial_checks,
                                   cam_est_minutes=cam.est_minutes(trial_path),
                                   note='Standalone first-layer trial from untouched stock. Production preview also begins at untouched stock; regenerate from measured completed height before continuing, do not concatenate blindly.'),
                tool_subsets=comparisons, path_scenarios=output,
                unknown_dimension_tools_excluded=['2cem-p-2.5d-1','2cem-p-2.5d-6','08-00501-00100'],
                execution_blockers=['Clamp solid positions/heights and holder dimensions not measured',
                                    'Machine coordinate travel, initial approach, ATC and exporter Z+40 moves unverified',
                                    'A6063 full-fin-width cutting conditions not trialled; D6 wear/runout/flutes to inspect',
                                    'Plate height/warp/zero uncertainty required before final allowance removal'],
                cut_order='EVERY fin each layer; never lower one fin all 75 mm while adjacent fins remain tall',
                trial='Same first whole layer at ap 0.05 mm and F180/RC6; inspect chips, adhesion, vibration; only then increase ap up to 0.2. No feed reduction alone as a cure for rubbing.')
    (HERE/'preparation.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    assembly = cq.Assembly(name='REFERENCE_ONLY_fins_up')
    assembly.add(target, name='PROTECTED_plate_393_320_10', color=cq.Color(.2,.6,.9))
    assembly.add(removed, name='REMOVE_fins_17', color=cq.Color(.9,.6,.15))
    assembly.save(str(HERE/'setup_reference.step'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    fig, ax = plt.subplots(figsize=(12,10))
    ax.add_patch(Rectangle((0,0),393,320,facecolor='#eeeeee',edgecolor='black'))
    for i,(x0,y0,x1,y1) in enumerate(fins,1):
        ax.add_patch(Rectangle((x0,y0),x1-x0,y1-y0,facecolor='#e9ab40'))
        ax.plot([-5,398],[(y0+y1)/2]*2,color='#176b9f',lw=.7)
        ax.text(402,(y0+y1)/2,str(i),fontsize=7,va='center')
    for y in (0,308):
        ax.add_patch(Rectangle((0,y),393,12,facecolor='green',alpha=.25))
    ax.set(xlim=(-15,418),ylim=(-10,330),xlabel='X / mm',ylabel='Y / mm',
           title='OFFLINE REFERENCE: 17 fin-only scans / green: candidate contact bands, fixtures unverified')
    ax.set_aspect('equal'); ax.grid(alpha=.2)
    fig.savefig(HERE/'setup_reference.png',dpi=150); plt.close(fig)
    print(json.dumps({k:data[k] for k in ['status','fin_count','remove_volume_mm3','rough_allowance_mm']},indent=2))
    for row in output:
        print(row['name'], 'cam_hours', round(row['cam_est_minutes']/60,2),
              'exporter_speed_hours', round(row['exporter_speed_est_minutes']/60,2),
              'checks', row['checks'])


if __name__ == '__main__':
    main()
