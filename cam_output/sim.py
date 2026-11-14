"""Height-map material removal simulation (tool-tip profile only: no shank/holder, no radial load).

Grid: square, centred on the part axis (same coordinates as cam.Path), set_grid(half, res).
  H, rep = simulate([(path, D, ball), ...], stock=(x0, x1, y0, y1))
      rep per path: (name, max Z depth of cut, first RAPID-CUTs, number of RAPID-CUTs, max doc per feed,
                     feed minutes in air, feed minutes cutting)  -> air share = wasted feed time
  ph = part_height(names, sect, zlist)        model top height per cell (-99 = no part)
  gouge(H, ph)        cells cut below the model
  leftover(H, ph, z)  cells outside the model still above z (uncut / accepted deviations)
  components(H, z)    connected pieces of material above height z (tabs / separation check)
"""
import numpy as np, math, os

def set_grid(half=55.0, res=0.2):
    global RES, HALF, N, xs, GX, GY
    RES, HALF = res, half
    N = int(round(2*half/res))
    xs = (np.arange(N) + 0.5)*res - half
    GX, GY = np.meshgrid(xs, xs, indexing='xy')
set_grid()

def part_height(names=('Inner_Core', 'Outer_Core'), sect=None, zlist=None, cache=None):
    """max model height per cell from horizontal sections. sect(name, Zm) -> shapely geometry in
    machine coordinates (default geom.sect_m = the flipped Core transform). The cache file name
    includes the model names and grid, so a different model or grid never reuses a stale map;
    pass cache= explicitly if sect/zlist differ for the same names."""
    default = tuple(names) == ('Inner_Core', 'Outer_Core') and sect is None and zlist is None \
        and (HALF, RES) == (55.0, 0.2)
    fn = cache or ('parth.npy' if default else f'parth_{"+".join(names)}_{HALF:g}_{RES:g}.npy')
    if os.path.exists(fn):
        h = np.load(fn)
        if h.shape == (N, N): return h
    import shapely
    if sect is None:
        from geom import sect_m as sect
    if zlist is None:
        zlist = list(np.arange(-0.01, -7.0, -0.5)) + list(np.arange(-7.0, -8.05, -0.02)) \
            + list(np.arange(-8.05, -15, -0.5)) + [-14.99]
    h = np.full((N, N), -99.0)
    for z in sorted(zlist):                    # ascending: higher sections overwrite
        for n in names:
            g = sect(n, z)
            if g.is_empty: continue
            inside = shapely.contains_xy(g, GX, GY)
            h[inside] = np.maximum(h[inside], z)
    np.save(fn, h); return h

def tool_profile(D, ball):
    R = D/2; n = int(math.ceil(R/RES)) + 1
    def prof(dx, dy):
        d = np.hypot(dx, dy)
        return np.where(d <= R, (R - np.sqrt(np.clip(R*R - d*d, 0, None))) if ball else 0.0, np.inf)
    return n, prof

def simulate(paths, stock_top=0.0, stock=(-50, 50, -50, 50), H=None):
    """stock: (xmin, xmax, ymin, ymax) of the blank in part-centred coordinates.
    Pass H from a previous call to continue from that state (e.g. after a separation point)."""
    if H is None:
        H = np.full((N, N), stock_top)
        x0, x1, y0, y1 = stock
        H[(GX < x0) | (GX > x1) | (GY < y0) | (GY > y1)] = -99
    report = []
    for p, D, ball in paths:
        n, prof = tool_profile(D, ball)
        maxdoc = 0; bad = []; prev = None; docs = {}; t_air = t_cut = 0.0
        for (x, y, z, f) in p.m:
            if prev is None: prev = (x, y, z); continue
            L = math.dist(prev, (x, y, z)); k = max(1, int(L/(RES*0.7)))
            for i in range(1, k + 1):
                t = i/k; px = prev[0] + (x - prev[0])*t; py = prev[1] + (y - prev[1])*t; pz = prev[2] + (z - prev[2])*t
                ci = int(round((px + HALF)/RES - 0.5)); cj = int(round((py + HALF)/RES - 0.5))
                if not (n <= ci < N - n and n <= cj < N - n):
                    raise ValueError(f'tool at ({px:.1f}, {py:.1f}) leaves the grid - call set_grid(larger half)')
                win = H[cj-n:cj+n+1, ci-n:ci+n+1]
                surf = pz + prof(xs[ci-n:ci+n+1][None, :] - px, xs[cj-n:cj+n+1][:, None] - py)
                doc = np.max(win - surf)
                if f is not None:
                    if doc > 1e-6: t_cut += L/k/f
                    else: t_air += L/k/f
                if doc > 1e-6:
                    if f is None: bad.append(('RAPID-CUT', round(px, 2), round(py, 2), round(pz, 2), round(doc, 3)))
                    maxdoc = max(maxdoc, doc); docs[f] = max(docs.get(f, 0), round(doc, 3))
                    np.minimum(win, surf, out=win)
            prev = (x, y, z)
        report.append((p.name, round(maxdoc, 3), bad[:5], len(bad), docs, round(t_air, 1), round(t_cut, 1)))
    return H, report

def _pts(mask, vals, k=5):
    idx = np.argsort(vals[mask])[::-1][:k]
    return [(round(float(GX[mask][i]), 2), round(float(GY[mask][i]), 2), round(float(vals[mask][i]), 3)) for i in idx]

def gouge(H, ph, tol=0.01, edge=1):
    """cells where material was removed below the model top. Cells within `edge` cells of a model
    edge/step are compared with the lowest neighbouring model height, so a path that touches the
    outline exactly (STL polygon vs grid) is not reported. returns (count, max depth, worst points,
    raw count without the edge allowance)"""
    from scipy import ndimage
    ref = ndimage.minimum_filter(ph, size=2*edge + 1, mode='nearest') if edge else ph
    raw = int(((ph > -90) & (H < ph - tol)).sum())
    m = (ref > -90) & (H < ref - tol)
    d = ref - H
    return (int(m.sum()), round(float(d[m].max()), 3) if m.any() else 0.0,
            _pts(m, d) if m.any() else [], raw)

def leftover(H, ph, z_floor, tol=0.05):
    """cells with no model above them where stock remains above z_floor (e.g. the through-depth
    or the intended floor). returns (count, max height above z_floor, worst points)"""
    m = (ph <= -90) & (H > -90) & (H > z_floor + tol)
    d = H - z_floor
    return int(m.sum()), round(float(d[m].max()), 3) if m.any() else 0.0, _pts(m, d) if m.any() else []

def components(H, z, min_cells=25):
    """pieces of material above height z (4-connected; diagonal contact is not a connection).
    returns sizes in mm^2, largest first, ignoring specks below min_cells."""
    from scipy import ndimage
    lab, n = ndimage.label(H > z)
    sizes = np.bincount(lab.ravel())[1:]
    return sorted((round(float(s)*RES*RES, 1) for s in sizes if s >= min_cells), reverse=True)
