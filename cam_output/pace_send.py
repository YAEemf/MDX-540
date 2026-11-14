"""Paced RML sender for long MDX-540 jobs.

Sends a large RML file in small pieces (separate RAW print jobs) only when the machine has
nearly consumed what it already holds, so the USB write never sits blocked on a full machine
buffer (the suspected cause of the corrupted-data "incorrect number of parameters" error).
Machine progress is read from the VPanel window (machine coordinates) and matched to the file.
If progress cannot be read, nothing more is sent (the machine just waits for data).

  python pace_send.py FILE --origin OX OY OZ [--piece 100000] [--lead 180] [--monitor]
  python pace_send.py FILE --origin 0 0 0 --plan     # show piece boundaries only (no printer)

  --origin : user origin in MACHINE coordinates (VPanel "Machine Coordinate System")
  --monitor: do not send; only track a job that is already running (for testing)
  --already-sent N must be used with the same --piece and the same script version
  (piece boundaries are chosen at high tool positions, not at fixed byte counts)
"""
import sys, re, math, time, argparse, datetime
import win32gui, win32print

PRN = 'Roland MDX-540'
ap = argparse.ArgumentParser()
ap.add_argument('file'); ap.add_argument('--origin', nargs=3, type=float, required=True)
ap.add_argument('--piece', type=int, default=100000)
ap.add_argument('--lead', type=float, default=180.0, help='send next piece when buffered motion < lead seconds')
ap.add_argument('--poll', type=float, default=5.0)
ap.add_argument('--monitor', action='store_true')
ap.add_argument('--already-sent', type=int, default=0, help='number of pieces already sent by a previous run')
ap.add_argument('--max-buffer', type=int, default=180000, help='max bytes sent but not yet executed')
ap.add_argument('--log', default=None)
ap.add_argument('--plan', action='store_true', help='only print the piece boundaries (Z where each piece ends), send nothing')
a = ap.parse_args()
OX, OY, OZ = a.origin
import os
logf = open(os.devnull if a.plan else (a.log or (a.file + '.pace.log')), 'a', encoding='utf-8')
def log(*s):
    m = datetime.datetime.now().strftime('%H:%M:%S ') + ' '.join(str(x) for x in s)
    print(m, flush=True); logf.write(m + '\n'); logf.flush()

# ---- parse file: line byte ranges, positions (user mm), cumulative est. time
raw = open(a.file, 'rb').read()
lines = raw.split(b'\r\n')
if lines and lines[-1] == b'': lines.pop()
pos, cum, ends = [], [], []
t = 0.0; v = None; prev = None; off = 0
for l in lines:
    off += len(l) + 2; ends.append(off)
    s = l.decode().rstrip(';')
    if s.startswith('V'): v = float(s[1:])
    m = re.fullmatch(r'Z(-?\d+),(-?\d+),(-?\d+)', s)
    if m:
        p = tuple(int(x) / 100 for x in m.groups())
        if prev is not None and v: t += math.dist(prev, p) / v
        prev = p
    pos.append(prev); cum.append(t)
assert ends[-1] == len(raw), (ends[-1], len(raw))
N = len(lines)
log(f'file {a.file}: {N} lines, {len(raw)} B, est {cum[-1]/60:.1f} min')

# piece boundaries (line index exclusive end), at most a.piece bytes each.
# If the sender stops, the machine halts at the end of the last piece with the spindle running,
# so each piece ends at the highest tool position among its last half (retract / air transit
# rather than mid-cut); ties go to the latest line.
cuts = []; start_b = 0; i0 = 0
while True:
    lim = next((i for i in range(i0, N) if ends[i] - start_b > a.piece), None)
    if lim is None: break
    lo = next(i for i in range(i0, lim + 1) if ends[i] - start_b >= a.piece // 2)
    best = max((i for i in range(lo, lim) if pos[i] is not None),
               key=lambda i: (pos[i][2], i), default=lim - 1)
    if best < i0: best = lim               # single line longer than piece (never in practice)
    cuts.append(best + 1); start_b = ends[best]; i0 = best + 1
if not cuts or cuts[-1] != N: cuts.append(N)
log('pieces: ' + ', '.join(f'{k+1}:{(ends[c-1]-(ends[cuts[k-1]-1] if k else 0))//1000}KB'
                           f'@Z{pos[c-1][2] if pos[c-1] else "?"}' for k, c in enumerate(cuts)))
if a.plan: sys.exit(0)

def _vpanel_xyz():
    hw = []
    win32gui.EnumWindows(lambda h, _: hw.append(h) if 'VPanel' in win32gui.GetWindowText(h) else None, None)
    if not hw: return None
    txt = []
    win32gui.EnumChildWindows(hw[0], lambda c, _: txt.append(win32gui.GetWindowText(c)), None)
    nums = [float(x) for x in txt if re.fullmatch(r'-?\d+\.\d{3}', x)]
    if len(nums) < 3: return None
    return (nums[0] - OX, nums[1] - OY, nums[2] - OZ)   # user coords

def vpanel_xyz():
    for _ in range(4):
        a1 = _vpanel_xyz(); time.sleep(0.05); a2 = _vpanel_xyz()
        if a1 and a2 and math.dist(a1, a2) < 0.3: return a2
    return None

import bisect
def match(p, ptr, hi_line, lo_line=None):
    """index of the file line whose segment (previous point -> this point) is closest to p,
    searching forward from ptr (allow small step back)"""
    lo = max(1, ptr - 200 if lo_line is None else lo_line); hi = min(N, hi_line)
    best, bi = 1e9, None
    for i in range(lo, hi):
        q1, q0 = pos[i], pos[i-1]
        if q1 is None or q0 is None: continue
        dx, dy, dz = q1[0]-q0[0], q1[1]-q0[1], 2*(q1[2]-q0[2])
        wx, wy, wz = p[0]-q0[0], p[1]-q0[1], 2*(p[2]-q0[2])
        L2 = dx*dx + dy*dy + dz*dz
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, (wx*dx + wy*dy + wz*dz)/L2))
        d = (wx-t*dx)**2 + (wy-t*dy)**2 + (wz-t*dz)**2
        if d < best: best, bi = d, i
    return bi, math.sqrt(best)

def queue_empty(h): return len(win32print.EnumJobs(h, 0, 50, 1)) == 0

def send_piece(h, k, s, e):
    data = raw[(ends[s-1] if s else 0):ends[e-1]]
    job = win32print.StartDocPrinter(h, 1, (f'{a.file.split(chr(92))[-1]} piece {k+1}/{len(cuts)}', None, 'RAW'))
    win32print.StartPagePrinter(h); n = win32print.WritePrinter(h, data)
    win32print.EndPagePrinter(h); win32print.EndDocPrinter(h)
    log(f'SENT piece {k+1}/{len(cuts)} lines {s}-{e-1} ({n} B, {(cum[e-1]-cum[s] if s < e else 0)/60:.1f} min) job {job}')

def unsent_bytes(h):
    return sum(j['Size'] for j in win32print.EnumJobs(h, 0, 50, 2) if not (j['Status'] & 0x80))

h = win32print.OpenPrinter(PRN, None if a.monitor else {'DesiredAccess': win32print.PRINTER_ALL_ACCESS})
k = a.already_sent
sent_end = N if a.monitor else (cuts[k-1] if k else 0)
start_xyz = vpanel_xyz()
log('start position (user)', start_xyz, 'pieces already sent', k)
if start_xyz is None and not a.monitor and k == 0:
    # without a start position the "machine has started moving" test never passes and no
    # second piece would ever be sent; refuse before sending anything
    sys.exit('VPanel position unreadable - open VPanel, show Machine Coordinate System, retry')
if not a.monitor and k == 0:
    if not queue_empty(h): sys.exit('queue not empty - refusing')
    win32print.SetPrinter(h, 0, None, win32print.PRINTER_CONTROL_RESUME)
    send_piece(h, 0, 0, cuts[0]); sent_end = cuts[0]; k = 1
moved = a.already_sent > 0          # a fresh run waits until the machine leaves its start position
ptr = None if a.already_sent > 0 else 0
t_good = time.time(); misses = 0; last_report = 0
while True:
    p = vpanel_xyz()
    if p is None:
        misses += 1; log('VPanel position unreadable', misses)
    else:
        if not moved and start_xyz and math.dist(p, start_xyz) > 1.0: moved = True; t_good = time.time()
        if not moved:
            i, d = None, 0.0
        elif ptr is None:                       # takeover: global search in sent data, strict
            i, d = match(p, 0, sent_end, lo_line=1)
            if i is not None and d < 0.2:
                ptr = i; t_good = time.time(); log(f'takeover: located at line {i} (d={d:.2f})')
            else:
                i = None
        else:                                   # time-gated search window
            horizon = cum[ptr] + (time.time() - t_good)*1.5 + 15
            hi_line = min(sent_end, bisect.bisect_right(cum, horizon) + 1)
            i, d = match(p, ptr, hi_line)
        if not moved or ptr is None:
            pass
        elif i is None or d > 0.6:
            misses += 1
            if misses % 6 == 1: log(f'no path match (pos user {tuple(round(x,2) for x in p)}, best d={d:.2f}) - holding')
        else:
            t_good = time.time()
            misses = 0; ptr = max(ptr, i)
            buf_b = ends[sent_end-1] - ends[ptr]
            buffered = cum[sent_end-1] - cum[ptr]
            if time.time() - last_report > 120:
                log(f'progress line {ptr}/{N} ({100*ptr/N:.1f}%), done {cum[ptr]/60:.1f}/{cum[-1]/60:.1f} min, buffered {buf_b} B / {buffered:.0f} s, d={d:.2f}')
                last_report = time.time()
            if not a.monitor and k < len(cuts):
                nxt = ends[cuts[k]-1] - ends[sent_end-1]
                if buf_b + nxt <= a.max_buffer and unsent_bytes(h) == 0:
                    send_piece(h, k, sent_end, cuts[k]); sent_end = cuts[k]; k += 1
                    log(f'  buffered after send {ends[sent_end-1]-ends[ptr]} B')
            if ptr >= N - 3 or (a.monitor and cum[-1] - cum[ptr] < 2):
                log('reached end of file'); break
    if misses > 120:
        log('too many misses - stopping sender (machine will wait for data)'); break
    time.sleep(a.poll)
win32print.ClosePrinter(h)
