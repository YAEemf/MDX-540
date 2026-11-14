"""2.5D toolpaths for the flipped Inner/Outer Core on MDX-540 (RML-1).

Coordinates: centered on part axis (machine X=50,Y=50 is part center), Z=0 stock top.
Move = (x, y, z, feed_mm_min or None for rapid)
"""
import math, numpy as np

CX, CY = 50.0, 50.0
Z_SAFE = 5.0      # above stock top, inside part area
Z_HIGH = 40.0     # first/last move height (clamps must stay below this)
Z_THRU = -15.4    # flat tool through-depth (0.4 mm into spoilboard)
Z_THRU_BALL = -16.0
CHORD = 0.005

class Path:
    def __init__(s, tool, rpm_code, name):
        s.tool, s.rpm_code, s.name, s.m = tool, rpm_code, name, []
    def rapid(s, x, y, z): s.m.append((x, y, z, None))
    def feed(s, x, y, z, f): s.m.append((x, y, z, f))
    @property
    def pos(s): return s.m[-1][:3]

def nseg(r, sweep=2*math.pi):
    if r < 1e-6: return 1
    th = 2*math.sqrt(2*CHORD/r)
    return max(8, int(math.ceil(abs(sweep)/th)))

def arc(p, r, a0, sweep, z0, z1, f):
    """arc/helix from angle a0 (current position must be at it), sweep>0 CCW"""
    n = nseg(r, sweep)
    for i in range(1, n+1):
        a = a0 + sweep*i/n
        p.feed(r*math.cos(a), r*math.sin(a), z0 + (z1-z0)*i/n, f)

def go_start(p, x, y, zdown, f_plunge):
    """retract, move above (x,y), plunge to zdown"""
    cx, cy, cz = p.pos if p.m else (x, y, Z_HIGH)
    if cz < Z_SAFE: p.rapid(cx, cy, Z_SAFE)
    p.rapid(x, y, Z_SAFE)
    p.feed(x, y, zdown, f_plunge)

def retract(p):
    x, y, z = p.pos
    p.rapid(x, y, Z_SAFE)

def levels(z_top, z_bot, ap):
    n = int(math.ceil((z_top - z_bot)/ap - 1e-9))
    return [z_top - (z_top - z_bot)*(i+1)/n for i in range(n)]

def spread(a, b, step):
    """values from a to b (inclusive of b, exclusive of a), equal steps <= step"""
    if abs(b-a) < 1e-9: return []
    n = int(math.ceil(abs(b-a)/step - 1e-9))
    return [a + (b-a)*(i+1)/n for i in range(n)]

def ring_pocket(p, r_in, r_out, lvls, z_start, ae, f_cut, f_ramp, r_mid=None):
    """concentric-ring clearing of an annulus of tool-centre radii [r_in,r_out].
    Each level: helical ramp on r_mid (CW), full CW circle, step outward rings CW (climb on
    outer wall), back to r_mid, step inward rings CCW (climb on inner wall)."""
    if r_mid is None: r_mid = 0.5*(r_in + r_out)
    outs, ins = spread(r_mid, r_out, ae), spread(r_mid, r_in, ae)
    go_start(p, r_mid, 0, z_start + 0.3, f_ramp)
    zprev = z_start + 0.3
    for i, z in enumerate(lvls):
        arc(p, r_mid, 0, -2*math.pi, zprev, z, f_ramp)          # continuous helical descent
        if i == len(lvls) - 1:
            arc(p, r_mid, 0, -2*math.pi, z, z, f_ramp)          # flat floor at final depth only
        for r in outs:
            p.feed(r, 0, z, f_cut); arc(p, r, 0, -2*math.pi, z, z, f_cut)
        if ins:
            p.feed(r_mid, 0, z, f_cut)
            for r in ins:
                p.feed(r, 0, z, f_cut); arc(p, r, 0, 2*math.pi, z, z, f_cut)
        p.feed(r_mid, 0, z, f_cut)
        zprev = z
    retract(p)

def wall_finish(p, r, lvls, ccw, lead, f, z_clear_from):
    """finish contour at tool-centre radius r, radial lead from r+lead (lead sign = side of clear)"""
    sweep = (2*math.pi + 0.1) * (1 if ccw else -1)
    for z in lvls:
        go_start(p, r + lead, 0, z_clear_from, f) if p.pos[2] >= Z_SAFE - 1e-9 else None
        p.feed(r + lead, 0, z, f)
        p.feed(r, 0, z, f)
        arc(p, r, 0, sweep, z, z, f)
        a = sweep
        p.feed((r + lead)*math.cos(a), (r + lead)*math.sin(a), z, f)
        p.feed(r + lead, 0, z, f)   # back to start radius along clear band (arc chord inside clear band)
    retract(p)

# ---------------- tools / conditions ----------------
def rc(rpm):
    """!RC code for this MDX-540: rpm ~= 400 + 773*n (n=0..15, measured 2026-10-04).
    Fusion's RML post assumes 3000 + 600*n, which is wrong for this machine."""
    return max(0, min(15, round((rpm - 400) / 773)))

# T1: 6 mm 3-flute flat, fz~0.02. code=3 is ~2720 rpm actual (the 4800 rpm target used the wrong
# mapping); kept so the 2026-10 Core files regenerate identically. New jobs: rc(5000) -> 6.
T1 = dict(D=6.0, code=3, f_cut=280, f_ramp=180, f_fin=250, f_helix=150, ap=0.3)
# T3: PRB2.0-21 2 mm ball. 12000 rpm (RC 15), fz~0.008
T3 = dict(D=2.0, code=15, f_cut=200, f_ramp=150)
ALLOW = 0.15

def job01():
    p = Path(1, T1['code'], '01_T1_bore_annulus')
    # center bore D10 through
    r = 5.0 - 3.0 - ALLOW
    go_start(p, r, 0, 0.5, T1['f_helix'])
    zs = levels(0.5, Z_THRU, T1['ap'])
    z0 = 0.5
    for z in zs:
        arc(p, r, 0, -2*math.pi, z0, z, T1['f_helix']); z0 = z
    arc(p, r, 0, -2*math.pi, Z_THRU, Z_THRU, T1['f_helix'])
    # finish bore r=2.0 CW (material outside)
    rf = 5.0 - 3.0
    retract(p)
    go_start(p, rf, 0, 0.3, T1['f_fin'])
    z0 = 0.3
    for z in levels(0.3, Z_THRU, 1.0):
        arc(p, rf, 0, -2*math.pi, z0, z, T1['f_fin']); z0 = z
    arc(p, rf, 0, -2*math.pi, Z_THRU, Z_THRU, T1['f_fin'])
    p.feed(rf - 0.5, 0, Z_THRU, T1['f_fin'])
    retract(p)
    # annulus r22..35 down to -7.0, tool centre band
    rin, rout = 22 + 3 + ALLOW, 35 - 3 - ALLOW
    ring_pocket(p, rin, rout, levels(0.0, -7.0, T1['ap']), 0.0, 1.2, T1['f_cut'], T1['f_ramp'])
    # finishing: hub wall r22 (CCW, clear side outward), outer-ring inner wall r35 (CW, clear inward)
    fl = levels(0.0, -7.0, 1.75)
    wall_finish(p, 25.0, fl, True, +0.6, T1['f_fin'], 0.3)
    wall_finish(p, 32.0, fl, False, -0.6, T1['f_fin'], 0.3)
    return p

# --- ball drop-cutter on axisymmetric envelope profile (r -> top height of part) ---
def H(r):
    if r < 5: return -99
    if r <= 22: return 0.0
    if r <= 25: return -8.0
    if r <= 28: return -8.0 + (r - 25)/3.0
    if r < 32: return -99
    if r <= 35: return -7.0
    if r <= 40: return 0.0
    return -99
def drop(R, rb=1.0, allow=0.02):
    best = -99
    for r in np.linspace(R - rb - allow, R + rb + allow, 801):
        d = min(abs(r - R), rb + allow)
        h = H(r)
        if h < -90 or d >= rb + allow - 1e-9: continue
        tip = h - rb + math.sqrt(max(0, (rb + allow)**2 - d*d)) - allow*0
        best = max(best, tip)
    return best

def allowed_intervals(Zk, lo=22.5, hi=33.5, dr=0.01):
    Rs = np.arange(lo, hi + 1e-9, dr)
    ok = np.array([drop(R) <= Zk + 1e-6 for R in Rs])
    iv, start = [], None
    for R, o in zip(Rs, ok):
        if o and start is None: start = R
        if not o and start is not None: iv.append((start, R - dr)); start = None
    if start is not None: iv.append((start, Rs[-1]))
    return [(a, b) for a, b in iv if b - a >= 0]

def band_pass(p, lvls, rm, step, sel, z_eval=None):
    """ball waterline rings. sel(a,b) picks the interval; rm fixed ramp ring inside every interval"""
    zprev = p.pos[2]
    for i, z in enumerate(lvls):
        ze = z if z_eval is None else z_eval(z)
        iv = [v for v in allowed_intervals(ze) if sel(*v)]
        assert len(iv) == 1 and iv[0][0] <= rm <= iv[0][1], (z, iv)
        a, b = iv[0]
        arc(p, rm, 0, -2*math.pi, zprev, z, T3['f_ramp'])
        if i == len(lvls) - 1:
            arc(p, rm, 0, -2*math.pi, z, z, T3['f_ramp'])
        for r in spread(rm, b, step):
            p.feed(r, 0, z, T3['f_cut']); arc(p, r, 0, -2*math.pi, z, z, T3['f_cut'])
        ins = spread(rm, a, step)
        if ins:
            p.feed(rm, 0, z, T3['f_cut'])
            for r in ins:
                p.feed(r, 0, z, T3['f_cut']); arc(p, r, 0, 2*math.pi, z, z, T3['f_cut'])
        p.feed(rm, 0, z, T3['f_cut'])
        zprev = z

def job02():
    p = Path(3, T3['code'], '02_T3_gap')
    # T1 has cleared tool-edge radii 22.15..34.85 down to Z-7.0, so Z-6.5 is free there
    # band A: hub root floor (Z-8) and tooth-tip slopes (r22..28, Z-7..-8)
    go_start(p, 24.0, 0, -6.5, T3['f_ramp'])
    p.feed(24.0, 0, -6.9, T3['f_ramp'])
    band_pass(p, levels(-7.0, -7.98, 0.98/7), 24.0, 0.3, lambda a, b: b < 28)
    # band B: 4 mm gap r28..32 through to spoilboard
    p.feed(24.0, 0, -6.5, 600); p.feed(30.0, 0, -6.5, 600); p.feed(30.0, 0, -6.9, T3['f_ramp'])
    band_pass(p, levels(-7.0, -8.0, 1/7.0) + levels(-8.0, Z_THRU_BALL, 0.15), 30.0, 1.0, lambda a, b: a > 28)
    retract(p)
    return p

def job03():
    p = Path(1, T1['code'], '03_T1_outer_separation')
    rin, rout = 40 + 3 + ALLOW, 44.15
    # channel: slot ring at rout (CW), then inner ring rin (CCW, climb on part wall)
    ring_pocket(p, rin, rout, levels(0.0, Z_THRU, T1['ap']), 0.0, 1.2, T1['f_cut'], T1['f_ramp'], r_mid=rout)
    fl = levels(0.0, Z_THRU, 2.0)
    wall_finish(p, 43.0, fl, True, +0.6, T1['f_fin'], 0.3)
    return p

# ---------------- RML writer ----------------
def rc_rpm(code): return 400 + 773*code

def check_rpm_code(code, rpm=None):
    """guard against the old 3000+600n mapping: code must be 0..15, match rpm if given,
    and not be below the cutting range (~3000 rpm)."""
    if not (isinstance(code, int) and 0 <= code <= 15):
        raise ValueError(f'!RC code {code!r} must be an int 0..15')
    if rpm is not None and rc(rpm) != code:
        raise ValueError(f'!RC{code} is ~{rc_rpm(code)} rpm, but {rpm} rpm needs !RC{rc(rpm)} (use cam.rc)')
    msg = f'!RC{code} ~ {rc_rpm(code)} rpm'
    if rc_rpm(code) < 3000: msg += '  WARNING: below the ~3000 rpm cutting range'
    print(msg)

def to_rml(p, path, rpm=None):
    """rpm: intended spindle speed; if given, p.rpm_code must equal rc(rpm)"""
    check_rpm_code(p.rpm_code, rpm)
    L = [';;^DF', '!MC0', 'V60.0', f'J{p.tool}', f'!RC{p.rpm_code}', '!MC1']
    def xyz(x, y, z): return f'Z{round((CX+x)*100)},{round((CY+y)*100)},{round(z*100)}'
    x0, y0, _ = p.m[0][:3]
    L.append('V20.0'); L.append(xyz(x0, y0, Z_HIGH))
    curv = None
    for (x, y, z, f) in p.m:
        v = 60.0 if f is None else f/60.0
        if z < 1.0 and f is None: v = 20.0   # never rapid fast below 1 mm
        vs = f'V{v:.1f}'
        if vs != curv: L.append(vs); curv = vs
        L.append(xyz(x, y, z))
    x, y, _ = p.m[-1][:3]
    L += ['V20.0', xyz(x, y, Z_HIGH), '!MC0', ';;^DF']
    # collapse duplicate consecutive coordinates
    out = []
    for s in L:
        if out and s == out[-1] and s.startswith('Z'): continue
        out.append(s)
    with open(path, 'w', newline='\r\n') as fh:
        fh.write(';\n'.join(out) + ';\n')
    return len(out)

def est_minutes(p):
    t, prev = 0.0, None
    for (x, y, z, f) in p.m:
        if prev is not None:
            d = math.dist(prev, (x, y, z))
            t += d / (3600.0 if f is None else f)
        prev = (x, y, z)
    return t

# ---------------- v2: chatter reduction for job 03 ----------------
def trochoid_ring(p, Rc, rho, adv, z_from, z_to, f, ramp_len=12.0, chord=0.01):
    """one revolution (CCW around part axis) of trochoidal loops; tool-centre radius Rc+-rho.
    z ramps from z_from to z_to over the first ramp_len mm of advance, the revolution overlap
    at the end re-cuts the ramp zone at full depth."""
    L = 2*math.pi*Rc + ramp_len          # overlap one ramp length
    nloop = int(math.ceil(L/adv))
    seg = max(12, int(math.ceil(2*math.pi/(2*math.sqrt(2*chord/rho)))))
    N = nloop*seg
    for i in range(N + 1):
        s = L*i/N; phi = 2*math.pi*i/seg
        th = s/Rc
        u = rho*math.sin(phi); v = rho*math.cos(phi)       # loop: starts at outer side
        r = Rc + v
        x = r*math.cos(th) - u*math.sin(th); y = r*math.sin(th) + u*math.cos(th)
        z = z_from + (z_to - z_from)*min(1.0, s/ramp_len)
        if i == 0:
            p.feed(x, y, p.pos[2], f); p.feed(x, y, z_from, f)
        else:
            p.feed(x, y, z, f)

def wall_finish2(p, r, lvls, ccw, lead, f, spring=True):
    sweep = (2*math.pi + 0.1) * (1 if ccw else -1)
    go_start(p, r + lead, 0, 0.3, f)
    for z in lvls + ([lvls[-1]] if spring else []):
        p.feed(r + lead, 0, z, f)
        p.feed(r, 0, z, f)
        arc(p, r, 0, sweep, z, z, f)
        p.feed((r + lead)*math.cos(sweep), (r + lead)*math.sin(sweep), z, f)
        p.feed(r + lead, 0, z, f)
    retract(p)

def job03b():
    p = Path(1, T1['code'], '03b_T1_outer_trochoid')
    Rc, rho = 44.15, 1.0            # tool centre 43.15..45.15 -> channel r40.15..48.15
    go_start(p, Rc + rho, 0, 0.3, T1['f_ramp'])
    zprev = 0.3
    for z in levels(0.0, Z_THRU, 1.0):
        trochoid_ring(p, Rc, rho, 0.4, zprev, z, 420)
        zprev = z
    retract(p)
    # outer wall finish r40 (CCW climb), full set of levels + spring pass at full depth
    fl = levels(0.0, Z_THRU, 2.0)
    wall_finish2(p, 43.0, fl, True, +0.6, T1['f_fin'])
    return p

def job03c():
    """03b reordered so the Outer core is never machined after it separates from the frame:
    trochoid channel to -14.4 (1 mm skin keeps Outer attached), wall finish to -14.4 while attached,
    then one final trochoid level at finish size (tool centre 43.0) that breaks through."""
    p = Path(1, T1['code'], '03c_T1_outer_trochoid_sep_last')
    Rc, rho, Z_SKIN = 44.15, 1.0, -14.4
    go_start(p, Rc + rho, 0, 0.3, T1['f_ramp'])
    zprev = 0.3
    for z in levels(0.0, Z_SKIN, 1.0):
        trochoid_ring(p, Rc, rho, 0.4, zprev, z, 420)
        zprev = z
    retract(p)
    wall_finish2(p, 43.0, levels(0.0, Z_SKIN, 2.0), True, +0.6, T1['f_fin'])
    # final breakthrough at finish size: tool centre 43.0..45.0
    go_start(p, 44.0 + rho, 0, Z_SKIN + 0.3, T1['f_ramp'])
    trochoid_ring(p, 44.0, rho, 0.4, Z_SKIN, Z_THRU, 420)
    retract(p)
    return p

# ---------------- v3 (2026-10-04): tooth slots widened to 2 mm + nipper tabs ----------------
SLOT_ANG = [math.radians(7.25 + 12*k) for k in range(30)]        # Inner & Outer slots share angles
TAB_ANG = [math.radians(a) for a in (49.25, 133.25, -130.75, -46.75)]   # mid-way between slots
TAB_TOP, TAB_HW = -14.6, 0.75     # tab = Z-15.0..-14.6, 1.5 mm wide
GAP_TABS = [(a, 27.5, 32.5) for a in TAB_ANG]
CHAN_TABS = [(a, 39.5, 48.7) for a in TAB_ANG]

def tab_dist(x, y, tabs):
    d = 1e9
    for a, r0, r1 in tabs:
        u = x*math.cos(a) + y*math.sin(a); v = -x*math.sin(a) + y*math.cos(a)
        du = max(r0 - u, 0, u - r1); dv = max(abs(v) - TAB_HW, 0)
        d = min(d, math.hypot(du, dv))
    return d

def apply_tabs(p, tabs, r_tool, margin=0.3):
    """clamp tool tip to TAB_TOP wherever the tool could reach a tab region"""
    keep = r_tool + margin
    out = []; prev = None
    for (x, y, z, f) in p.m:
        if prev is not None and f is not None:
            L = math.dist(prev[:2], (x, y))
            near = min(tab_dist(prev[0], prev[1], tabs), tab_dist(x, y, tabs)) < keep + L + 0.5
            n = int(math.ceil(L / 0.15)) if near and L > 0.15 else 1
            for i in range(1, n + 1):
                t = i / n
                xi, yi, zi = prev[0] + (x-prev[0])*t, prev[1] + (y-prev[1])*t, prev[2] + (z-prev[2])*t
                if zi < TAB_TOP and tab_dist(xi, yi, tabs) < keep: zi = TAB_TOP
                out.append((xi, yi, zi, f))
        else:
            out.append((x, y, z, f))
        prev = (x, y, z)
    p.m = out
    return p

def simplify(p, tol=0.002):
    """drop moves that lie on the straight line between their neighbours (same feed, within tol mm).
    apply_tabs subdivides every segment near a tab into 0.15 mm steps; most of them stay collinear.
    Explicit call (not inside apply_tabs) so older jobs regenerate byte-identically."""
    m = p.m
    if len(m) < 3: return p
    out = [m[0]]
    for i in range(1, len(m) - 1):
        a, b, c = out[-1], m[i], m[i+1]
        if b[3] == c[3] and b[3] is not None and a[3] == b[3]:
            ab = [b[k] - a[k] for k in range(3)]; ac = [c[k] - a[k] for k in range(3)]
            L2 = sum(v*v for v in ac)
            if L2 > 0:
                t = sum(ab[k]*ac[k] for k in range(3))/L2
                if 0 <= t <= 1 and math.dist(b[:3], [a[k] + t*ac[k] for k in range(3)]) <= tol:
                    continue
        out.append(m[i])
    out.append(m[-1])
    p.m = out
    return p

def slots_pass(p, zlist, f=160, f_air=600):
    """each slot angle: radial centre line r26..r34 (Inner root r25 .. Outer slot bottom r35),
    zig-zag: in at z_k, out at z_k+1 (both legs cut), separately for the Inner and Outer legs"""
    for a in SLOT_ANG:
        c, s = math.cos(a), math.sin(a)
        P = lambda r, z: (r*c, r*s, z)
        x, y, z = p.pos
        p.feed(x, y, -6.5, f_air); p.feed(*P(30.0, -6.5), f_air)     # transit above floor (cleared band)
        for r_end in (26.0, 34.0):
            p.feed(*P(30.0, zlist[0] + 0.15), f)
            for k in range(0, len(zlist), 2):
                za = zlist[k]; zb = zlist[k+1] if k + 1 < len(zlist) else za
                p.feed(*P(30.0, za), f)
                p.feed(*P(r_end, za), f)
                p.feed(*P(r_end, zb), f)
                p.feed(*P(30.0, zb), f)
            p.feed(*P(30.0, -6.5), f_air)

def job02t(k_resume=48):
    lvB = levels(-7.0, -8.0, 1/7.0) + levels(-8.0, Z_THRU_BALL, 0.15)
    p = Path(3, T3['code'], '02t_T3_gap_tabs_slots')
    go_start(p, 30.0, 0, -6.5, T3['f_ramp'])
    p.feed(30.0, 0, lvB[k_resume-1], T3['f_ramp'])
    band_pass(p, lvB[k_resume:], 30.0, 1.0, lambda a, b: a > 28)
    p.feed(30.0, 0, -6.5, 600)
    slots_pass(p, levels(-7.0, Z_THRU_BALL, 0.15))
    retract(p)
    return apply_tabs(p, GAP_TABS, 1.0)

def job03t():
    p = job03c(); p.name = '03t_T1_outer_trochoid_tabs'
    return apply_tabs(p, CHAN_TABS, 3.0)

def slots_pass_ramp(p, angles, z_top=-7.0, z_bot=Z_THRU_BALL, dz=0.1, f=150, f_air=600):
    """slot legs without vertical plunges: every leg (in and out) ramps down dz along the slot,
    so the ball never plunges end-on into closed material. max engagement depth ~2*dz at the
    material entry side."""
    for a in angles:
        c, s = math.cos(a), math.sin(a)
        P = lambda r, z: (r*c, r*s, z)
        x, y, z = p.pos
        if z < -6.5: p.feed(x, y, -6.5, f_air)
        p.feed(*P(30.0, -6.5), f_air)
        for r_end in (26.0, 34.0):
            zc = z_top
            p.feed(*P(30.0, zc), f)
            at_end = False
            while zc > z_bot + 1e-9:
                zc = max(z_bot, zc - dz)
                p.feed(*P(r_end if not at_end else 30.0, zc), f)
                at_end = not at_end
            # final flat legs at full depth (both directions) to clean the last ramp
            p.feed(*P(30.0 if at_end else r_end, zc), f)
            p.feed(*P(r_end if at_end else 30.0, zc), f)
            x, y, _ = p.pos
            p.feed(*P(30.0, zc), f)
            p.feed(*P(30.0, -6.5), f_air)

def job02s(first_slot=16):
    p = Path(3, T3['code'], f'02s_T3_slots_from{first_slot+1}')
    a0 = SLOT_ANG[first_slot]
    go_start(p, 30.0*math.cos(a0), 30.0*math.sin(a0), -6.5, T3['f_ramp'])
    slots_pass_ramp(p, SLOT_ANG[first_slot:])
    retract(p)
    return apply_tabs(p, GAP_TABS, 1.0)

# ---------------- Bottom_Simple: 2 mm SUS430 plate, 2 mm ball only ----------------
BS_TABS_ANG = [math.radians(a) for a in (45, 135, 225, 315)]
BS_TAB_TOP = -1.6                          # plate Z0..-2.0, tab = -2.0..-1.6
BS_TABS = [(a, 39.3, 41.7) for a in BS_TABS_ANG]

def bs_outline():
    from geom import sect_orig
    import shapely
    g = sect_orig('Bottom_Simple', -6.0)
    g = shapely.affinity.translate(g, 0.038*0, 0)          # model is centred on (0,0)
    return g

def contour_ramp(p, ring_xy, z_top, z_bot, dz, f):
    """continuous helical ramp along a closed polyline (tool-centre), then one flat lap"""
    pts = ring_xy
    seg = [math.dist(pts[i], pts[(i+1) % len(pts)]) for i in range(len(pts))]
    lap = sum(seg)
    z = z_top
    p.feed(pts[0][0], pts[0][1], z, f)
    while z > z_bot + 1e-9:
        z0 = z; z1 = max(z_bot, z - dz); s = 0.0
        for i in range(len(pts)):
            s += seg[i]; q = pts[(i+1) % len(pts)]
            p.feed(q[0], q[1], z0 + (z1 - z0)*s/lap, f)
        z = z1
    for i in range(len(pts)):                      # flat lap at full depth
        q = pts[(i+1) % len(pts)]; p.feed(q[0], q[1], z, f)

def line_ramp(p, A, B, z_top, z_bot, dz, f):
    """zig-zag ramp along segment A-B (no vertical plunge), ends with flat passes"""
    z = z_top; at_B = False
    p.feed(A[0], A[1], z, f)
    while z > z_bot + 1e-9:
        z = max(z_bot, z - dz); T = B if not at_B else A
        p.feed(T[0], T[1], z, f); at_B = not at_B
    T = A if at_B else B; p.feed(T[0], T[1], z, f)
    T = B if at_B else A; p.feed(T[0], T[1], z, f)

def job_bottom(dz=0.15, f=150):
    import shapely
    p = Path(3, 15, 'BS_T3_bottom_simple')
    ZB = -3.0
    # 1) centre hole D10: rings r1..r4 (ball centre), ramp on r2.5
    p.m.append((2.5, 0, Z_HIGH, None))
    go_start(p, 2.5, 0, 0.3, 120)
    zprev = 0.3
    lv = levels(0.0, ZB, dz)
    for i, z in enumerate(lv):
        arc(p, 2.5, 0, -2*math.pi, zprev, z, 120)
        if i == len(lv) - 1: arc(p, 2.5, 0, -2*math.pi, z, z, 120)
        for r in (3.25, 4.0):
            p.feed(r, 0, z, f); arc(p, r, 0, -2*math.pi, z, z, f)
        p.feed(2.5, 0, z, f)
        for r in (1.75, 1.0):
            p.feed(r, 0, z, f); arc(p, r, 0, 2*math.pi, z, z, f)
        p.feed(2.5, 0, z, f)
        zprev = z
    retract(p)
    # 2) notch centre line: from outside the part (x=42.5) to the root-arc centre (x=24.794)
    go_start(p, 42.5, 0, 0.3, 120)
    line_ramp(p, (42.5, 0), (24.794, 0), 0.0, ZB, 0.1, f)
    retract(p)
    # 3) outer contour: part outline offset +1.0 (tool centre), CCW (part on the left = climb)
    g = bs_outline()
    off = g.exterior.buffer(0)  # placeholder
    poly = shapely.Polygon(g.exterior).buffer(1.0, quad_segs=64, join_style='mitre' if False else 'round')
    ring = list(poly.exterior.coords)[:-1]
    if shapely.LinearRing(ring).is_ccw is False: ring = ring[::-1]
    # densify to <=0.3 mm and start at angle 180 deg
    dense = []
    for i in range(len(ring)):
        a = ring[i]; b = ring[(i+1) % len(ring)]
        n = max(1, int(math.ceil(math.dist(a, b)/0.3)))
        for k in range(n): dense.append((a[0] + (b[0]-a[0])*k/n, a[1] + (b[1]-a[1])*k/n))
    k0 = min(range(len(dense)), key=lambda i: math.dist(dense[i], (-41.0, 0.0)))
    dense = dense[k0:] + dense[:k0]
    go_start(p, dense[0][0], dense[0][1], 0.3, 120)
    contour_ramp(p, dense, 0.0, ZB, dz, f)
    retract(p)
    # tabs on the outer contour only
    global TAB_TOP
    keep = TAB_TOP; TAB_TOP = BS_TAB_TOP
    apply_tabs(p, BS_TABS, 1.0)
    TAB_TOP = keep
    return p

def job_bottom_flat6(dz=0.2, f=180, f_ramp=120, ZB=-2.3):
    """Bottom_Simple with the 6 mm flat only (ATC1), 4200 rpm (RC2).
    hole D10: helix r2.0 (CW). notch (6.0 wide, R3 root): single centre-line slot, zig-zag ramp.
    outer: circle r43 (tool centre) CCW, helical ramp, 4 nipper tabs."""
    p = Path(1, 2, 'BS_T1_flat6')
    p.m.append((2.0, 0, Z_HIGH, None))
    go_start(p, 2.0, 0, 0.3, f_ramp)
    z0 = 0.3
    for z in levels(0.3, ZB, dz):
        arc(p, 2.0, 0, -2*math.pi, z0, z, f_ramp); z0 = z
    arc(p, 2.0, 0, -2*math.pi, ZB, ZB, f_ramp)
    p.feed(1.0, 0, ZB, f_ramp)
    retract(p)
    go_start(p, 43.0, 0, 0.3, f_ramp)
    line_ramp(p, (43.0, 0), (24.794, 0), 0.0, ZB, 0.15, f_ramp)
    retract(p)
    R = 43.0
    n = nseg(R)
    ring = [(R*math.cos(math.pi + 2*math.pi*i/n), R*math.sin(math.pi + 2*math.pi*i/n)) for i in range(n)]   # CCW from 180 deg
    go_start(p, ring[0][0], ring[0][1], 0.3, f_ramp)
    contour_ramp(p, ring, 0.0, ZB, dz, f)
    retract(p)
    global TAB_TOP
    keep = TAB_TOP; TAB_TOP = BS_TAB_TOP
    apply_tabs(p, [(a, 39.3, 46.7) for a in BS_TABS_ANG], 3.0)
    TAB_TOP = keep
    return p
