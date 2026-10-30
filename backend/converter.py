"""Conversor basico de SVG/DXF a G-code.

No es FlatCAM, pero cubre casos simples:
  - SVG: line, rect, circle, ellipse, polyline, polygon, path (basico)
  - DXF: LINE, CIRCLE, ARC

Todo se convierte a movimientos G0/G1.
"""

import re
import math
import xml.etree.ElementTree as ET


def _svg_parse_float_list(s):
    parts = re.split(r"[,\s]+", s.strip())
    return [float(x) for x in parts if x]


def _svg_get_transform_offset(elem):
    tr = elem.get("transform", "")
    dx = dy = 0.0
    m = re.search(r"translate\(\s*([-\d.]+)[\s,]+([-\d.]+)\s*\)", tr)
    if m:
        dx, dy = float(m.group(1)), float(m.group(2))
    return dx, dy


def svg_to_paths(svg_text, scale_mm_per_unit=1.0):
    paths = []
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as e:
        raise ValueError(f"SVG no valido: {e}")

    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]

    def walk(el, accum_dx=0.0, accum_dy=0.0):
        dx, dy = _svg_get_transform_offset(el)
        accum_dx += dx
        accum_dy += dy
        tag = el.tag

        if tag == "line":
            x1 = float(el.get("x1", 0)) * scale_mm_per_unit + accum_dx
            y1 = float(el.get("y1", 0)) * scale_mm_per_unit + accum_dy
            x2 = float(el.get("x2", 0)) * scale_mm_per_unit + accum_dx
            y2 = float(el.get("y2", 0)) * scale_mm_per_unit + accum_dy
            paths.append([(x1, y1), (x2, y2)])

        elif tag == "rect":
            x = float(el.get("x", 0)) * scale_mm_per_unit + accum_dx
            y = float(el.get("y", 0)) * scale_mm_per_unit + accum_dy
            w = float(el.get("width", 0)) * scale_mm_per_unit
            h = float(el.get("height", 0)) * scale_mm_per_unit
            paths.append([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)])

        elif tag == "circle":
            cx = float(el.get("cx", 0)) * scale_mm_per_unit + accum_dx
            cy = float(el.get("cy", 0)) * scale_mm_per_unit + accum_dy
            r = float(el.get("r", 0)) * scale_mm_per_unit
            _add_circle(paths, cx, cy, r)

        elif tag == "ellipse":
            cx = float(el.get("cx", 0)) * scale_mm_per_unit + accum_dx
            cy = float(el.get("cy", 0)) * scale_mm_per_unit + accum_dy
            rx = float(el.get("rx", 0)) * scale_mm_per_unit
            ry = float(el.get("ry", 0)) * scale_mm_per_unit
            _add_ellipse(paths, cx, cy, rx, ry)

        elif tag in ("polyline", "polygon"):
            pts_raw = el.get("points", "")
            coords = _svg_parse_float_list(pts_raw)
            pts = []
            for i in range(0, len(coords) - 1, 2):
                pts.append((coords[i] * scale_mm_per_unit + accum_dx,
                            coords[i + 1] * scale_mm_per_unit + accum_dy))
            if tag == "polygon" and pts:
                pts.append(pts[0])
            if len(pts) >= 2:
                paths.append(pts)

        elif tag == "path":
            d = el.get("d", "")
            new_paths = _svg_parse_path_d(d, scale_mm_per_unit, accum_dx, accum_dy)
            for p in new_paths:
                if len(p) >= 2:
                    paths.append(p)

        for child in el:
            walk(child, accum_dx, accum_dy)

    walk(root)
    return paths


def _add_circle(paths, cx, cy, r, segments=64):
    pts = []
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    paths.append(pts)


def _add_ellipse(paths, cx, cy, rx, ry, segments=64):
    pts = []
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    paths.append(pts)


def _svg_parse_path_d(d, scale, dx, dy):
    paths = []
    current = []
    cx = cy = 0.0
    start_x = start_y = 0.0
    tokens = re.findall(r"[MLHVCZmlhvcz]|-?\d*\.?\d+(?:e[-+]?\d+)?", d)

    i = 0
    cmd = None
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in ("Z", "z"):
                if current:
                    current.append((start_x, start_y))
                    paths.append(current)
                    current = []
                continue

        if cmd in ("M", "L"):
            if i + 1 < len(tokens):
                x = float(tokens[i]) * scale + dx
                y = float(tokens[i + 1]) * scale + dy
                i += 2
                if cmd == "M":
                    if current:
                        paths.append(current)
                    current = [(x, y)]
                    start_x, start_y = x, y
                    cmd = "L"
                else:
                    current.append((x, y))
        elif cmd in ("m", "l"):
            if i + 1 < len(tokens):
                x = cx + float(tokens[i]) * scale
                y = cy + float(tokens[i + 1]) * scale
                i += 2
                if cmd == "m":
                    if current:
                        paths.append(current)
                    current = [(x, y)]
                    start_x, start_y = x, y
                    cmd = "l"
                else:
                    current.append((x, y))
        elif cmd in ("H", "h"):
            if i < len(tokens):
                x = float(tokens[i]) * scale + (dx if cmd == "H" else cx)
                i += 1
                current.append((x, cy))
        elif cmd in ("V", "v"):
            if i < len(tokens):
                y = float(tokens[i]) * scale + (dy if cmd == "V" else cy)
                i += 1
                current.append((cx, y))
        else:
            i += 1
            continue

        if current:
            cx, cy = current[-1]

    if current:
        paths.append(current)
    return paths


def dxf_to_paths(dxf_text, scale_mm_per_unit=1.0):
    paths = []
    lines = [ln.strip() for ln in dxf_text.splitlines()]
    pairs = []
    i = 0
    while i + 1 < len(lines):
        try:
            code = int(lines[i])
            val = lines[i + 1]
            pairs.append((code, val))
            i += 2
        except ValueError:
            i += 1

    entities = []
    current = None
    in_entities = False

    for code, val in pairs:
        if code == 0 and val == "SECTION":
            continue
        if code == 2 and val == "ENTITIES":
            in_entities = True
            continue
        if code == 0 and val == "ENDSEC":
            if in_entities and current:
                entities.append(current)
                current = None
            in_entities = False
            continue
        if not in_entities:
            continue
        if code == 0:
            if current:
                entities.append(current)
            current = {"type": val, "data": {}}
        elif current is not None:
            current["data"][code] = val

    if current:
        entities.append(current)

    for ent in entities:
        t = ent["type"]
        d = ent["data"]

        if t == "LINE":
            x1 = float(d.get(10, 0)) * scale_mm_per_unit
            y1 = float(d.get(20, 0)) * scale_mm_per_unit
            x2 = float(d.get(11, 0)) * scale_mm_per_unit
            y2 = float(d.get(21, 0)) * scale_mm_per_unit
            paths.append([(x1, y1), (x2, y2)])

        elif t == "CIRCLE":
            cx = float(d.get(10, 0)) * scale_mm_per_unit
            cy = float(d.get(20, 0)) * scale_mm_per_unit
            r = float(d.get(40, 0)) * scale_mm_per_unit
            _add_circle(paths, cx, cy, r)

        elif t == "ARC":
            cx = float(d.get(10, 0)) * scale_mm_per_unit
            cy = float(d.get(20, 0)) * scale_mm_per_unit
            r = float(d.get(40, 0)) * scale_mm_per_unit
            a1 = float(d.get(50, 0))
            a2 = float(d.get(51, 0))
            _add_arc(paths, cx, cy, r, a1, a2)

    return paths


def _add_arc(paths, cx, cy, r, a_start, a_end, segments=32):
    a1 = math.radians(a_start)
    a2 = math.radians(a_end)
    if a2 < a1:
        a2 += 2 * math.pi
    pts = []
    for i in range(segments + 1):
        a = a1 + (a2 - a1) * i / segments
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    paths.append(pts)


def paths_to_gcode(paths, feed=300.0, rapid=3000.0,
                   pen_up=2.0, pen_down=0.0,
                   origin_x=0.0, origin_y=0.0):
    lines = []
    lines.append("; CNC IDE -- Generado desde SVG/DXF")
    lines.append("; =====================================")
    lines.append(f"; Paths: {len(paths)}")
    lines.append("G21          ; milimetros")
    lines.append("G90          ; coordenadas absolutas")
    lines.append(f"G0 Z{pen_up:.3f} F{rapid:.1f}")

    total_dist = 0.0

    for path in paths:
        if len(path) < 2:
            continue
        x0, y0 = path[0]
        lines.append(f"G0 X{x0 + origin_x:.3f} Y{y0 + origin_y:.3f}")
        lines.append(f"G1 Z{pen_down:.3f} F{feed:.1f}")

        prev_x, prev_y = x0, y0
        for (px, py) in path[1:]:
            lines.append(f"G1 X{px + origin_x:.3f} Y{py + origin_y:.3f} F{feed:.1f}")
            d = math.hypot(px - prev_x, py - prev_y)
            total_dist += d
            prev_x, prev_y = px, py

        lines.append(f"G1 Z{pen_up:.3f} F{rapid:.1f}")

    lines.append(f"G0 X{origin_x:.3f} Y{origin_y:.3f}")
    lines.append("M30          ; fin de programa")
    lines.append(f"; Distancia total: {total_dist:.1f} mm")

    return lines
