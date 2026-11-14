"""Tool-choice comparison BEFORE writing toolpaths (2.5D layers, shapely).

For every candidate plan (= a primary tool, with smaller tools for what it cannot reach) prints
the estimated cutting time and the area no tool can reach, so the fastest acceptable plan is
chosen from numbers, not from eyeballed feature widths (Bottom_Simple's notch was judged "~5 mm,
6 mm tool does not fit" by eye; it is 6.0 mm, and a 2 mm-only plan took 57 min instead of 24).

    import plan_tools as pt
    from geom import sect_orig
    pt.compare(lambda z: sect_orig('Bottom_Simple', z - 5.0), zb=[0, -2.0], through=True)
    pt.compare(lambda z: geom.parts_m(z), zb=[0, -7, -8, -15], through=True)   # Core

Model of the work (per layer between consecutive zb, section taken at the layer middle):
  - outline: the outer boundary of each part is profiled with a slot of the tool's width
    (cut-out from the stock; the stock outside is kept as frame). mode='pocket' instead removes
    everything outside the part within `stock` (facing / open shapes).
  - large holes (a slug >= slug_min mm2 would remain inside a tool-width channel) are cut out
    along their boundary like the outline; the slug needs tabs or must be allowed to drop.
  - pockets: the remaining holes, gaps and notch bottoms are cleared, largest tool first; a smaller tool only gets what larger tools cannot
    reach (morphological opening with the tool radius).
  - concave features of the outline narrower than the outline tool become pockets for the
    smaller tools.
Times are rough (constant MRR / slot feed from 機械・工具データ.md, no air moves): use them to
RANK plans; est_minutes() of the real path is the number to report. Keep TOOLS in sync with
機械・工具データ.md (conditions = what has worked; tested=False = guessed).
"""
import math, shapely
from shapely.ops import unary_union
from shapely.geometry import Polygon, box

EPS = 0.02      # a tool of diameter D is taken to fit a slot of width D (full-width slot)

TOOLS = [
    # reach = usable depth below stock top (flute / neck length minus ~1 mm margin)
    dict(name='T1 6mm flat', D=6.0, ball=False, reach=19.0, ap=0.3, ae=1.2, F=280, ap_slot=0.2, F_slot=180, tested=True),
    dict(name='PRB2.0-21 2mm ball', D=2.0, ball=True, reach=20.0, ap=0.15, ae=1.0, F=200, ap_slot=0.1, F_slot=150, tested=True),
    dict(name='3mm ball (flute 3)', D=3.0, ball=True, reach=2.5, ap=0.15, ae=1.2, F=200, ap_slot=0.1, F_slot=150, tested=False),
    dict(name='AC40 1mm square', D=1.0, ball=False, reach=13.0, ap=0.05, ae=0.3, F=100, ap_slot=0.03, F_slot=80, tested=False),
]

def _filled(g):
    polys = [g] if g.geom_type == 'Polygon' else list(getattr(g, 'geoms', []))
    return unary_union([Polygon(p.exterior) for p in polys if not p.is_empty])

def _open(g, r):
    return g.buffer(-(r - EPS)).buffer(r - EPS) if r > EPS else g

def _close(g, r):
    return g.buffer(r - EPS).buffer(-(r - EPS))

def _layers(sect, zb):
    out = []
    for zt, zl in zip(zb[:-1], zb[1:]):
        g = sect(0.5*(zt + zl))
        out.append((zt, zl, g if g is not None else Polygon()))
    return out

def _over(t, through):
    """extra depth below the part bottom for a through cut: ball tools need their radius"""
    return (t['D']/2 if t['ball'] else 0.4) if through else 0.0

def _polys(g):
    return [g] if g.geom_type == 'Polygon' else [p for p in getattr(g, 'geoms', []) if p.geom_type == 'Polygon']

def plan(sect, zb, tools, through=True, mode='profile', stock=None, slug_min=20.0):
    """tools: list ordered large -> small, tools[0] (first one deep enough) does the outline.
    Holes large enough to leave a slug of >= slug_min mm2 inside a tool-width channel are cut
    out along their boundary (slug falls out / needs tabs) instead of being pocketed."""
    t_min = 0.0; per_tool = {t['name']: 0.0 for t in tools}; lost = 0.0; notes = []; slugs = 0
    layers = _layers(sect, zb)
    for li, (zt, zl, part) in enumerate(layers):
        h = zt - zl; last = li == len(layers) - 1
        ok = [t for t in tools if t['reach'] >= -zl + (_over(t, through) if last else 0.0)]
        if part.is_empty: continue
        if not ok:      # infeasible: everything to remove in this layer stays
            notes.append(f'layer {zt}..{zl}: no tool reaches {-zl:.1f} mm (+through allowance)')
            lost += _filled(part).difference(part).area*h; continue
        def slot_time(t, length):
            return length*math.ceil(h/t['ap_slot'] - 1e-9)/t['F_slot']
        if mode == 'pocket':
            region = (stock if stock is not None else box(*part.bounds).buffer(10)).difference(part)
        else:
            filled = _filled(part)
            o = ok[0]; r = o['D']/2
            # outline: slot of the outline tool around the (closed) outer boundary
            outer = _close(filled, r)
            tt = slot_time(o, sum(p.buffer(r).exterior.length for p in _polys(outer)))
            per_tool[o['name']] += tt; t_min += tt
            region = outer.difference(filled)
            for hole in _polys(filled.difference(part)):
                if hole.buffer(-o['D']).area >= slug_min:       # cut out along the boundary
                    inner = hole.buffer(-r)
                    tt = slot_time(o, sum(p.exterior.length for p in _polys(inner)))
                    per_tool[o['name']] += tt; t_min += tt; slugs += li == 0
                    region = region.union(hole.difference(_open(hole, r)))   # corners it cannot reach
                else:
                    region = region.union(hole)
        remaining = region
        for t in ok:
            reach = _open(region, t['D']/2).intersection(remaining)
            if reach.area > 1e-6:
                mrr = t['ap']*min(t['ae'], t['D'])*t['F']
                tt = reach.area*h/mrr
                per_tool[t['name']] += tt; t_min += tt
                remaining = remaining.difference(reach)
        lost += remaining.area*h
    return dict(tools=[t['name'] for t in tools if per_tool[t['name']] >= 0.05] or [tools[0]['name']], minutes=round(t_min, 1),
                per_tool={k: round(v, 1) for k, v in per_tool.items() if v >= 0.05},
                unreachable_mm3=round(lost, 2), slugs=slugs, notes=notes)

def compare(sect, zb, tools=None, through=True, mode='profile', stock=None, show=True, slug_min=20.0):
    """every subset of the tools (largest = outline tool); helpers that end up unused are dropped,
    and plans that use the same tools are shown once (the one reaching most)"""
    import itertools
    tools = sorted(tools or TOOLS, key=lambda t: -t['D'])
    best_by_tools = {}
    for n in range(1, len(tools) + 1):
        for combo in itertools.combinations(tools, n):
            r = plan(sect, zb, list(combo), through, mode, stock, slug_min)
            k = tuple(r['tools'])
            if k not in best_by_tools or r['unreachable_mm3'] < best_by_tools[k]['unreachable_mm3']:
                best_by_tools[k] = r
    res = list(best_by_tools.values())
    # plans that reach (almost) as much as the best one come first, fastest first; then the rest
    feas = [r['unreachable_mm3'] for r in res if not r['notes']]
    best = min(feas) if feas else min(r['unreachable_mm3'] for r in res)
    same = lambda r: not r['notes'] and r['unreachable_mm3'] <= best*1.05 + 0.5
    res.sort(key=lambda r: (not same(r), bool(r['notes']), r['minutes']))
    if show:
        print(f'{"min":>7} {"unreach mm3":>11}  plan (per-tool min)')
        for k, r in enumerate(res):
            if k and same(res[k-1]) and not same(r): print('  --- below: less of the shape is reached, or infeasible ---')
            per = ', '.join(f'{k} {v}' for k, v in r['per_tool'].items())
            print(f'{r["minutes"]:7.1f} {r["unreachable_mm3"]:11.2f}  {" + ".join(r["tools"])}  [{per}]'
                  + (f'  slugs {r["slugs"]} (need tabs)' if r['slugs'] else '')
                  + (f'  NOTE {r["notes"]}' if r['notes'] else ''))
        untested = [t['name'] for t in tools if not t['tested']]
        if untested: print('untested conditions (guessed):', ', '.join(untested))
    return res

def min_width(sect, z, tools=None):
    """largest listed tool that fits every concave feature/hole at height z (diagnostic)"""
    part = sect(z); filled = _filled(part); holes = filled.difference(part)
    for t in sorted(tools or TOOLS, key=lambda t: -t['D']):
        r = t['D']/2
        lost = (_close(filled, r).difference(filled).area + holes.difference(_open(holes, r)).area)
        print(f'{t["name"]:22s} D{t["D"]}: unreachable area {lost:.2f} mm2 at z={z}')
