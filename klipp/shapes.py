"""Recognise rough hand-drawn strokes as clean shapes.

recognize() takes the points of a freehand stroke and returns one of
    ("line", x1, y1, x2, y2)
    ("ellipse", cx, cy, rx, ry, angle_degrees)
    ("rect", x, y, width, height)
or None when the stroke doesn't look enough like any of them.
"""

import math

MIN_SIZE = 24  # strokes smaller than this (diagonal, in scene px) are left alone


def _bounds(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _length(points):
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def _as_line(points):
    start, end = points[0], points[-1]
    chord = math.dist(start, end)
    if chord < MIN_SIZE:
        return None
    dx, dy = (end[0] - start[0]) / chord, (end[1] - start[1]) / chord
    deviation = max(abs((p[0] - start[0]) * dy - (p[1] - start[1]) * dx) for p in points)
    if deviation <= max(4.0, 0.06 * chord) and _length(points) <= 1.2 * chord:
        return ("line", start[0], start[1], end[0], end[1])
    return None


def _is_closed(points, width, height):
    """The stroke comes back to where it began (a loop may overshoot its start a little)."""
    early = points[: max(2, len(points) // 4)]
    gap = min(math.dist(points[-1], p) for p in early)
    return gap <= 0.3 * max(width, height) and _length(points) >= 1.5 * (width + height)


def _rotate(points, angle, origin):
    c, s = math.cos(angle), math.sin(angle)
    ox, oy = origin
    return [((x - ox) * c - (y - oy) * s + ox, (x - ox) * s + (y - oy) * c + oy) for x, y in points]


def _ellipse_error(points):
    """Mean radial error of the points against the ellipse filling their bounding box."""
    x0, y0, x1, y1 = _bounds(points)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = max((x1 - x0) / 2, 1.0), max((y1 - y0) / 2, 1.0)
    error = sum(abs(math.hypot((x - cx) / rx, (y - cy) / ry) - 1) for x, y in points) / len(points)
    return error, (cx, cy, rx, ry)


def _principal_angle(points):
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    syy = sum((p[1] - my) ** 2 for p in points)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    return 0.5 * math.atan2(2 * sxy, sxx - syy), (mx, my)


def _as_ellipse(points):
    """Fit axis-aligned, and along the stroke's main axis in case it's drawn tilted."""
    error, (cx, cy, rx, ry) = _ellipse_error(points)
    best = (error, cx, cy, rx, ry, 0.0)
    angle, center = _principal_angle(points)
    if abs(angle) > math.radians(4):
        rotated = _rotate(points, -angle, center)
        error, (rcx, rcy, rx, ry) = _ellipse_error(rotated)
        if error < best[0] * 0.8:
            (cx, cy), = _rotate([(rcx, rcy)], angle, center)
            best = (error, cx, cy, rx, ry, math.degrees(angle))
    error, cx, cy, rx, ry, angle = best
    if error > 0.13:
        return None
    return ("ellipse", cx, cy, rx, ry, angle)


def _as_rect(points):
    x0, y0, x1, y1 = _bounds(points)
    w, h = x1 - x0, y1 - y0
    if min(w, h) < MIN_SIZE / 2:
        return None
    scale = min(w, h)
    error = sum(min(abs(x - x0), abs(x - x1), abs(y - y0), abs(y - y1)) for x, y in points) / len(points) / scale
    # A rectangle has ink near its corners; a circle never does.
    near = 0.15
    corners = sum(
        1 for x, y in points
        if min(abs(x - x0), abs(x - x1)) < near * w and min(abs(y - y0), abs(y - y1)) < near * h
    )
    if error <= 0.06 and corners >= 0.06 * len(points):
        return ("rect", x0, y0, w, h)
    return None


def recognize(points, allow=("line", "ellipse", "rect")):
    points = [(float(x), float(y)) for x, y in points]
    if len(points) < 5:
        return None
    x0, y0, x1, y1 = _bounds(points)
    w, h = x1 - x0, y1 - y0
    if math.hypot(w, h) < MIN_SIZE:
        return None
    if "line" in allow and (line := _as_line(points)):
        return line
    if not _is_closed(points, w, h):
        return None
    if "rect" in allow and (rect := _as_rect(points)):
        return rect
    if "ellipse" in allow and (ellipse := _as_ellipse(points)):
        return ellipse
    return None
