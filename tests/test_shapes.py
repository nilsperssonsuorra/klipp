"""Checks the shape recogniser against synthetic hand-drawn strokes.

Run: .venv\\Scripts\\python tests\\test_shapes.py
"""

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from klipp.shapes import recognize

rng = random.Random(7)
failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def jitter(points, amount):
    return [(x + rng.uniform(-amount, amount), y + rng.uniform(-amount, amount)) for x, y in points]


def loop(cx, cy, rx, ry, tilt=0.0, overshoot=30, wobble=0.04, n=60, start=-115):
    t = math.radians(tilt)
    out = []
    for i in range(n):
        f = i / (n - 1)
        a = math.radians(start + (360 + overshoot) * f)
        k = 1 + wobble * math.sin(f * math.pi * 3) + 0.05 * f
        x, y = rx * k * math.cos(a), ry * k * math.sin(a)
        out.append((cx + x * math.cos(t) - y * math.sin(t), cy + x * math.sin(t) + y * math.cos(t)))
    return out


def rough_rect(x, y, w, h, n=15, noise=2.5):
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x + 4, y + 3)]
    out = []
    for (ax, ay), (bx, by) in zip(corners, corners[1:]):
        out += [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(n)]
    return jitter(out, noise)


shape = recognize(loop(300, 200, 150, 60))
check(shape and shape[0] == "ellipse", f"wobbly overshooting loop -> ellipse ({shape and shape[0]})")
if shape and shape[0] == "ellipse":
    _, cx, cy, rx, ry, angle = shape
    check(abs(cx - 300) < 12 and abs(cy - 200) < 12 and abs(rx - 155) < 20 and abs(ry - 62) < 12,
          f"ellipse lands where it was drawn ({cx:.0f},{cy:.0f} r={rx:.0f}x{ry:.0f})")

shape = recognize(jitter(loop(300, 200, 90, 90, overshoot=15), 2))
check(shape and shape[0] == "ellipse", "rough circle -> ellipse")

shape = recognize(loop(300, 200, 160, 50, tilt=25))
check(shape and shape[0] == "ellipse" and abs(shape[5] - 25) < 8,
      f"tilted loop -> tilted ellipse (angle {shape[5]:.0f})" if shape and shape[0] == "ellipse" else "tilted loop -> ellipse")

shape = recognize(rough_rect(100, 100, 300, 160))
check(shape and shape[0] == "rect", f"rough rectangle -> rect ({shape and shape[0]})")

line = jitter([(100 + i * 10, 300 + i * 4) for i in range(40)], 2)
shape = recognize(line)
check(shape and shape[0] == "line", f"shaky straight stroke -> line ({shape and shape[0]})")

scribble = [(100 + i * 8, 300 + 40 * math.sin(i * 0.9)) for i in range(40)]
check(recognize(scribble) is None, "zig-zag scribble stays freehand")

u_shape = [(100 + 100 * math.cos(a), 100 + 100 * math.sin(a)) for a in (math.pi * i / 30 for i in range(31))]
check(recognize(u_shape) is None, "open U shape stays freehand")

check(recognize([(10, 10), (12, 11), (14, 12), (15, 12), (16, 13)]) is None, "tiny stroke stays freehand")

check(recognize(line, allow=("ellipse",)) is None, "shapes that aren't allowed are not returned")

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
