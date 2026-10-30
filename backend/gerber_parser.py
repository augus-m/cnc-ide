"""Parser de Gerber RS-274X con shapely + macro RoundRect."""

import re
import math
from dataclasses import dataclass, field
from typing import List, Optional

from shapely.geometry import (
    Point, LineString, Polygon, MultiPolygon, box
)
from shapely.ops import unary_union


@dataclass
class Aperture:
    code: int
    shape: str
    params: List[float] = field(default_factory=list)
    macro_name: str = ""

    def get_radius(self):
        if self.shape == 'C' and self.params:
            return self.params[0] / 2.0
        if self.shape == 'R' and len(self.params) >= 2:
            return max(self.params[0], self.params[1]) / 2.0
        if self.shape == 'O' and len(self.params) >= 2:
            return max(self.params[0], self.params[1]) / 2.0
        if self.shape == 'P' and self.params:
            return self.params[0] / 2.0
        return 0.2


@dataclass
class Primitive:
    kind: str
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    cx: float = 0.0
    cy: float = 0.0
    cw: bool = True
    aperture: Optional[Aperture] = None
    width: float = 0.0


class GerberParser:
    def __init__(self):
        self.units_mm = True
        self.int_digits = 3
        self.dec_digits = 6
        self.apertures = {}
        self.current_aperture = None
        self.current_x = 0.0
        self.current_y = 0.0
        self.interpolation = 1
        self.primitives = []
        self.warnings = []
        self.bounds = None

    def parse(self, text):
        self.primitives = []
        self.warnings = []
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        parts = []
        i = 0
        while i < len(text):
            if text[i] == '%':
                end = text.find('%', i + 1)
                if end < 0:
                    parts.append(text[i:])
                    break
                parts.append(text[i:end + 1])
                i = end + 1
            else:
                next_pct = text.find('%', i)
                if next_pct < 0:
                    parts.append(text[i:])
                    break
                parts.append(text[i:next_pct])
                i = next_pct

        for part in parts:
            if part.startswith('%'):
                self._process_extended_block(part)
            else:
                self._process_regular_block(part)

        self._compute_bounds()
        return self.primitives

    def _process_extended_block(self, block):
        content = block.strip('%')
        commands = [c.strip() for c in content.split('*') if c.strip()]
        for cmd in commands:
            cmd = cmd.strip()
            if not cmd:
                continue
            if cmd.startswith('FS'):
                self._parse_fs(cmd)
            elif cmd.startswith('MO'):
                self._parse_mo(cmd)
            elif cmd.startswith('AD'):
                self._parse_ad(cmd)

    def _parse_fs(self, cmd):
        m = re.match(r"FS([LT]?)([AI]?)X(\d)(\d)Y(\d)(\d)", cmd)
        if m:
            self.int_digits = int(m.group(3))
            self.dec_digits = int(m.group(4))

    def _parse_mo(self, cmd):
        if cmd.startswith('MOMM'):
            self.units_mm = True
        elif cmd.startswith('MOIN'):
            self.units_mm = False

    def _parse_ad(self, cmd):
        """Parsea %ADD..% incluyendo macros."""
        # Intentar con parametros: ADD12RoundRect,0.25X...
        m = re.match(r"ADD(\d+)([A-Za-z_][A-Za-z0-9_]*),([\d.X\-]+)", cmd)
        if m:
            code = int(m.group(1))
            shape = m.group(2)
            params_str = m.group(3)
            params = []
            for p in params_str.split('X'):
                try:
                    params.append(float(p))
                except ValueError:
                    pass

            # Forma estandar
            if shape in ('C', 'R', 'O', 'P'):
                self.apertures[code] = Aperture(code, shape, params)
            else:
                # Macro personalizada (RoundRect, Obround, etc.)
                self.apertures[code] = Aperture(code, 'MACRO', params, shape)
            return

        # Sin parametros: ADD10C
        m = re.match(r"ADD(\d+)([A-Za-z_][A-Za-z0-9_]*)$", cmd)
        if m:
            code = int(m.group(1))
            shape = m.group(2)
            self.apertures[code] = Aperture(code, 'C', [0.5])

    def _process_regular_block(self, block):
        commands = [c.strip() for c in block.split('*') if c.strip()]
        for cmd in commands:
            self._process_command(cmd)

    def _process_command(self, cmd):
        cmd = cmd.strip()
        if not cmd:
            return
        if cmd.startswith('M02') or cmd.startswith('M00'):
            return
        if cmd.startswith('G01'):
            self.interpolation = 1
            return
        if cmd.startswith('G02'):
            self.interpolation = 2
            return
        if cmd.startswith('G03'):
            self.interpolation = 3
            return
        if cmd.startswith('G74') or cmd.startswith('G75'):
            return
        if cmd.startswith('G36') or cmd.startswith('G37'):
            return

        # Cambio de apertura
        m = re.match(r"^D(\d+)$", cmd)
        if m:
            code = int(m.group(1))
            if code >= 10:
                if code in self.apertures:
                    self.current_aperture = self.apertures[code]
                else:
                    self.warnings.append(f"Apertura D{code} no definida")
            return

        # Coordenadas + operacion
        m = re.match(
            r"^(?:G0[123])?"
            r"(?:X(-?\d+))?"
            r"(?:Y(-?\d+))?"
            r"(?:I(-?\d+))?"
            r"(?:J(-?\d+))?"
            r"D0([123])$",
            cmd
        )
        if m:
            x = self._coord(m.group(1), self.current_x)
            y = self._coord(m.group(2), self.current_y)
            i = self._coord(m.group(3), 0.0)
            j = self._coord(m.group(4), 0.0)
            op = int(m.group(5))

            if op == 1:
                self._draw_to(x, y, i, j)
            elif op == 2:
                self.current_x = x
                self.current_y = y
            elif op == 3:
                self._flash_at(x, y)
                self.current_x = x
                self.current_y = y

    def _coord(self, s, current):
        if s is None:
            return current
        sign = -1 if s.startswith('-') else 1
        s_abs = s.lstrip('+-').lstrip('0') or '0'
        if '.' in s_abs:
            value = float(s_abs)
        else:
            value = int(s_abs) / (10 ** self.dec_digits)
        if not self.units_mm:
            value *= 25.4
        return sign * value

    def _draw_to(self, x, y, i, j):
        if self.current_aperture is None:
            return
        if self.interpolation == 1:
            self.primitives.append(Primitive(
                kind='line',
                x1=self.current_x, y1=self.current_y,
                x2=x, y2=y,
                aperture=self.current_aperture,
                width=self.current_aperture.get_radius() * 2,
            ))
        else:
            cx = self.current_x + i
            cy = self.current_y + j
            self.primitives.append(Primitive(
                kind='arc',
                x1=self.current_x, y1=self.current_y,
                x2=x, y2=y,
                cx=cx, cy=cy,
                cw=(self.interpolation == 2),
                aperture=self.current_aperture,
                width=self.current_aperture.get_radius() * 2,
            ))
        self.current_x = x
        self.current_y = y

    def _flash_at(self, x, y):
        if self.current_aperture is None:
            return
        self.primitives.append(Primitive(
            kind='flash',
            x1=x, y1=y, x2=x, y2=y,
            aperture=self.current_aperture,
        ))

    def _compute_bounds(self):
        if not self.primitives:
            self.bounds = None
            return
        xs, ys = [], []
        for p in self.primitives:
            xs.extend([p.x1, p.x2])
            ys.extend([p.y1, p.y2])
            if p.aperture:
                r = p.aperture.get_radius()
                xs.extend([p.x1 - r, p.x2 + r])
                ys.extend([p.y1 - r, p.y2 + r])
        self.bounds = (min(xs), min(ys), max(xs), max(ys))


def _arc_points(x1, y1, x2, y2, cx, cy, cw, segments=24):
    r = math.hypot(x1 - cx, y1 - cy)
    a1 = math.atan2(y1 - cy, x1 - cx)
    a2 = math.atan2(y2 - cy, x2 - cx)
    if cw:
        while a2 >= a1:
            a2 -= 2 * math.pi
    else:
        while a2 <= a1:
            a2 += 2 * math.pi
    pts = []
    for i in range(segments + 1):
        t = i / segments
        a = a1 + (a2 - a1) * t
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _primitive_to_shapely(p):
    """Convierte primitiva a geometria shapely."""
    if p.kind == 'line':
        if p.width > 0:
            return LineString([(p.x1, p.y1), (p.x2, p.y2)]).buffer(
                p.width / 2.0, cap_style=1, join_style=1
            )
        return LineString([(p.x1, p.y1), (p.x2, p.y2)])

    if p.kind == 'arc':
        pts = _arc_points(p.x1, p.y1, p.x2, p.y2, p.cx, p.cy, p.cw, segments=32)
        if p.width > 0:
            return LineString(pts).buffer(p.width / 2.0, cap_style=1, join_style=1)
        return LineString(pts)

    if p.kind == 'flash':
        ap = p.aperture
        if ap is None:
            return None

        # Formas estandar
        if ap.shape == 'C':
            r = ap.params[0] / 2.0 if ap.params else 0.2
            return Point(p.x1, p.y1).buffer(r, resolution=16)

        if ap.shape == 'R':
            w = ap.params[0] if ap.params else 0.5
            h = ap.params[1] if len(ap.params) > 1 else w
            return box(p.x1 - w/2, p.y1 - h/2, p.x1 + w/2, p.y1 + h/2)

        if ap.shape == 'O':
            w = ap.params[0] if ap.params else 0.5
            h = ap.params[1] if len(ap.params) > 1 else w
            r = min(w, h) / 2.0
            inner = box(p.x1 - w/2 + r, p.y1 - h/2 + r,
                        p.x1 + w/2 - r, p.y1 + h/2 - r)
            return inner.buffer(r, resolution=8)

        if ap.shape == 'P':
            d = ap.params[0] if ap.params else 0.5
            n = int(ap.params[1]) if len(ap.params) > 1 else 6
            r = d / 2.0
            pts = []
            for i in range(n):
                a = 2 * math.pi * i / n + math.pi / 2
                pts.append((p.x1 + r * math.cos(a), p.y1 + r * math.sin(a)))
            return Polygon(pts)

        # MACRO personalizada
        if ap.shape == 'MACRO':
            if ap.macro_name == 'RoundRect' and len(ap.params) >= 9:
                # Formato: [radio, x1,y1, x2,y2, x3,y3, x4,y4, flag]
                # Las esquinas son RELATIVAS al punto de flash
                radius = ap.params[0]
                corners = [
                    (ap.params[1], ap.params[2]),
                    (ap.params[3], ap.params[4]),
                    (ap.params[5], ap.params[6]),
                    (ap.params[7], ap.params[8]),
                ]
                abs_corners = [(p.x1 + cx, p.y1 + cy) for (cx, cy) in corners]
                xs = [c[0] for c in abs_corners]
                ys = [c[1] for c in abs_corners]
                minx, maxx = min(xs), max(xs)
                miny, maxy = min(ys), max(ys)

                if radius > 0 and (maxx - minx) > 2*radius and (maxy - miny) > 2*radius:
                    inner = box(minx + radius, miny + radius,
                                maxx - radius, maxy - radius)
                    return inner.buffer(radius, resolution=8)
                else:
                    return box(minx, miny, maxx, maxy)

            # Otra macro desconocida: aproximar a un cuadrado pequeño
            return Point(p.x1, p.y1).buffer(0.5, resolution=8)

    return None


def gerber_to_paths(gerber_text, tool_diameter=0.2, clearance=0.15,
                    simplify_tol=0.005):
    parser = GerberParser()
    primitives = parser.parse(gerber_text)

    if not primitives:
        return [], None, {"error": "Sin primitivas"}

    geoms = []
    for p in primitives:
        g = _primitive_to_shapely(p)
        if g is not None and not g.is_empty:
            geoms.append(g)

    if not geoms:
        return [], parser.bounds, {"error": "Sin geometrias validas"}

    copper = unary_union(geoms)

    offset = tool_diameter / 2.0 + clearance
    isolation = copper.buffer(offset, join_style=2, mitre_limit=2.0)

    # Douglas-Peucker: el error maximo respecto al contorno es simplify_tol.
    # Quita los puntos casi alineados (que hacen frenar la maquina en cada
    # tramo). simplify_tol=0 lo desactiva.
    if simplify_tol and simplify_tol > 0.0:
        isolation = isolation.simplify(simplify_tol, preserve_topology=True)

    paths = []
    if isolation.is_empty:
        return [], parser.bounds, {"error": "Buffer vacio"}

    if isolation.geom_type == 'Polygon':
        polys = [isolation]
    elif isolation.geom_type == 'MultiPolygon':
        polys = list(isolation.geoms)
    elif isolation.geom_type == 'GeometryCollection':
        polys = [g for g in isolation.geoms if g.geom_type == 'Polygon']
    else:
        polys = []

    for poly in polys:
        exterior = list(poly.exterior.coords)
        if len(exterior) >= 2:
            paths.append(exterior)
        for interior in poly.interiors:
            coords = list(interior.coords)
            if len(coords) >= 2:
                paths.append(coords)

    if paths:
        all_x = [pt[0] for path in paths for pt in path]
        all_y = [pt[1] for path in paths for pt in path]
        bounds = (min(all_x), min(all_y), max(all_x), max(all_y))
    else:
        bounds = parser.bounds

    return paths, bounds, {
        "primitives": len(primitives),
        "geometries": len(geoms),
        "paths": len(paths),
        "warnings": parser.warnings,
        "bounds": bounds,
    }


def gerber_to_gcode(gerber_text, tool_diameter=0.2, clearance=0.15,
                    feed=300.0, rapid=3000.0, pen_up=2.0, pen_down=0.0,
                    origin_x=0.0, origin_y=0.0, simplify_tol=0.005):
    paths, bounds, info = gerber_to_paths(gerber_text, tool_diameter, clearance,
                                          simplify_tol=simplify_tol)

    if not paths:
        raise ValueError("No se pudieron extraer paths del Gerber")

    offset_x = origin_x
    offset_y = origin_y
    if bounds:
        minx, miny, _, _ = bounds
        offset_x = origin_x - minx
        offset_y = origin_y - miny

    lines = []
    lines.append("; CNC IDE -- Gerber a G-code (shapely)")
    lines.append("; =====================================")
    lines.append(f"; Primitivas: {info['primitives']}")
    lines.append(f"; Paths: {len(paths)}")
    lines.append(f"; Tool: {tool_diameter:.2f} mm, clearance: {clearance:.2f} mm")
    lines.append("G21")
    lines.append("G90")
    lines.append(f"G0 Z{pen_up:.3f} F{rapid:.1f}")

    total_dist = 0.0

    for path in paths:
        if len(path) < 2:
            continue
        x0, y0 = path[0]
        lines.append(f"G0 X{x0 + offset_x:.3f} Y{y0 + offset_y:.3f}")
        lines.append(f"G1 Z{pen_down:.3f} F{feed:.1f}")
        prev_x, prev_y = path[0]
        for (px, py) in path[1:]:
            d = math.hypot(px - prev_x, py - prev_y)
            total_dist += d
            lines.append(f"G1 X{px + offset_x:.3f} Y{py + offset_y:.3f} F{feed:.1f}")
            prev_x, prev_y = px, py
        lines.append(f"G1 Z{pen_up:.3f} F{rapid:.1f}")

    lines.append(f"G0 X{origin_x:.3f} Y{origin_y:.3f}")
    lines.append("M30")
    lines.append(f"; Distancia total: {total_dist:.1f} mm")

    return lines, info
