"""Prepend a vertical lift (current XY, Z+40 user) to an RML file, using the live VPanel position.

  python make_liftfirst.py BASE.rml OUT.rml --origin OX OY OZ [--area XMIN XMAX YMIN YMAX]

The machine must be idle (after Stop Cutting / tool change) and VPanel must show
"Machine Coordinate System". The lift is inserted before the J (tool) command, so the tool leaves
the work vertically before any tool change or XY move.
"""
import sys, re, time, math, argparse, win32gui

ap = argparse.ArgumentParser()
ap.add_argument('base'); ap.add_argument('out')
ap.add_argument('--origin', nargs=3, type=float, required=True)
ap.add_argument('--area', nargs=4, type=float, default=(-15, 95, -10, 100), metavar=('XMIN', 'XMAX', 'YMIN', 'YMAX'),
                help='allowed current position in USER coords (sanity check of origin / display mode)')
a = ap.parse_args()

def read():
    hw = []
    win32gui.EnumWindows(lambda h, _: hw.append(h) if 'VPanel' in win32gui.GetWindowText(h) else None, None)
    t = []
    win32gui.EnumChildWindows(hw[0], lambda c, _: t.append(win32gui.GetWindowText(c)), None)
    return [float(x) for x in t if re.fullmatch(r'-?\d+\.\d{3}', x)][:3]

r1 = read(); time.sleep(1.0); r2 = read()
if len(r1) < 3 or math.dist(r1, r2) > 0.005:
    sys.exit(f'machine not idle or VPanel unreadable: {r1} -> {r2}')
ux, uy, uz = r2[0] - a.origin[0], r2[1] - a.origin[1], r2[2] - a.origin[2]
print(f'machine {r2} -> user ({ux:.2f}, {uy:.2f}, {uz:.2f})')
if not (a.area[0] <= ux <= a.area[1] and a.area[2] <= uy <= a.area[3]):
    sys.exit('position outside the stock area - check origin / VPanel coordinate system')
L = open(a.base, 'rb').read().decode().split(';\r\n')
assert L[:4] == [';;^DF', '!MC0', 'V60.0', L[3]] and L[3].startswith('J'), L[:4]
lift_z = max(40.0, uz)          # never move down
L = L[:2] + ['V10.0', f'Z{round(ux*100)},{round(uy*100)},{round(lift_z*100)}'] + L[2:]
open(a.out, 'wb').write(';\r\n'.join(L).encode())
print('wrote', a.out, 'first moves:', L[2:5])
