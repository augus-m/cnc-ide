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
    macro_body: List = field(default_factory=list)

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
        self.in_region = False
        self.region_contours = []
        self.region_cur = []
        self.regions = []
        self.macros = {}
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
        if content.startswith('AM'):
            self._parse_am(content)
            return
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

    def _parse_am(self, content):
        """Guarda la definicion de una macro %AM (sin comentarios '0 ...')."""
        lines = [l.strip() for l in content.split('*') if l.strip()]
        name = lines[0][2:].strip()
        body = [l for l in lines[1:] if not l.startswith('0')]
        self.macros[name] = body

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
                self.apertures[code] = Aperture(code, 'MACRO', params, shape,
                                                self.macros.get(shape, []))
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
        if cmd.startswith('G36'):
            self.in_region = True
            self.region_contours = []
            self.region_cur = []
            return
        if cmd.startswith('G37'):
            self._end_region()
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

            if self.in_region and op in (1, 2):
                if op == 2:
                    self._region_close()
                    self.region_cur = [(x, y)]
                else:
                    if not self.region_cur:
                        self.region_cur = [(self.current_x, self.current_y)]
                    self.region_cur.append((x, y))
                self.current_x = x
                self.current_y = y
                return
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

    def _region_close(self):
        if len(self.region_cur) >= 3:
            self.region_contours.append(self.region_cur)
        self.region_cur = []

    def _end_region(self):
        from shapely.geometry import Polygon
        self._region_close()
        res = None
        for c in self.region_contours:
            p = Polygon(c).buffer(0)
            res = p if res is None else res.symmetric_difference(p)
        if res is not None and not res.is_empty:
            self.regions.append(res)
        self.region_contours = []
        self.in_region = False

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


def _eval_macro_body(body, params):
    """Evalua una macro %AM de Gerber. Devuelve la geometria centrada en (0,0)
    en mm, o None si no hay nada que dibujar."""
    import re as _re
    from shapely.geometry import Point, Polygon, LineString, box
    from shapely.affinity import rotate

    def val(expr):
        def repl(m):
            n = int(m.group(1))
            return repr(float(params[n - 1])) if 0 < n <= len(params) else "0"
        txt = _re.sub(r"\$(\d+)", repl, expr.strip())
        txt = txt.replace("x", "*").replace("X", "*")
        if not _re.fullmatch(r"[0-9.eE+\-*/() ]+", txt):
            return 0.0
        try:
            return float(eval(txt, {"__builtins__": {}}, {}))
        except Exception:
            return 0.0

    result = None
    for line in body:
        parts = line.split(",")
        code = parts[0].strip()
        args = [val(t) for t in parts[1:]]
        if not args:
            continue
        exposure = args[0]
        g = None
        try:
            if code == "1":                       # circulo: exp, diam, cx, cy
                d, cx, cy = args[1], args[2], args[3]
                g = Point(cx, cy).buffer(d / 2.0, resolution=16)
            elif code == "21":                    # rectangulo centrado: exp, w, h, cx, cy, rot
                w, h, cx, cy = args[1], args[2], args[3], args[4]
                rot = args[5] if len(args) > 5 else 0.0
                g = box(cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
                if rot:
                    g = rotate(g, rot, origin=(0, 0))
            elif code == "20":                    # linea vectorial: exp, ancho, x1, y1, x2, y2, rot
                wd = args[1]
                x1, y1, x2, y2 = args[2], args[3], args[4], args[5]
                rot = args[6] if len(args) > 6 else 0.0
                g = LineString([(x1, y1), (x2, y2)]).buffer(wd / 2.0, cap_style=2, join_style=2)
                if rot:
                    g = rotate(g, rot, origin=(0, 0))
            elif code == "4":                     # contorno: exp, n, x1,y1 ... xn,yn, rot
                n = int(round(args[1]))
                coords = args[2:2 + 2 * (n + 1)]
                rot = args[2 + 2 * (n + 1)] if len(args) > 2 + 2 * (n + 1) else 0.0
                pts = [(coords[k], coords[k + 1]) for k in range(0, len(coords) - 1, 2)]
                if len(pts) >= 4 and pts[0] == pts[-1]:
                    pts = pts[:-1]
                g = Polygon(pts)
                if rot:
                    g = rotate(g, rot, origin=(0, 0))
            elif code == "5":                     # poligono regular: exp, n, cx, cy, d, rot
                n = int(round(args[1]))
                cx, cy, d = args[2], args[3], args[4]
                rot = args[5] if len(args) > 5 else 0.0
                pts = [(cx + d / 2.0 * math.cos(2 * math.pi * k / n + math.radians(rot)),
                        cy + d / 2.0 * math.sin(2 * math.pi * k / n + math.radians(rot)))
                       for k in range(n)]
                g = Polygon(pts)
        except Exception:
            g = None
        if g is None or g.is_empty:
            continue
        if exposure >= 0.5:                       # exposicion 1: material
            result = g if result is None else result.union(g)
        elif result is not None:                  # exposicion 0: se borra
            result = result.difference(g)
    return result


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
        if ap.shape == 'MACRO' and ap.macro_body:
            from shapely.affinity import translate
            g = _eval_macro_body(ap.macro_body, ap.params)
            if g is None or g.is_empty:
                return None
            return translate(g, xoff=p.x1, yoff=p.y1)
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


def _hatch_paths(copper, tool_diameter, pitch):
    """Relleno en serpentina continua. Las lineas horizontales se unen con un
    tramo vertical dentro del cobre: la pluma baja una vez por cadena y hace
    el zigzag, en vez de levantarse entre cada pasada (eso dejaba puntos)."""
    from shapely.geometry import LineString
    inner = copper.buffer(-tool_diameter / 2.0, join_style=2, mitre_limit=2.0)
    if inner.is_empty:
        return []
    minx, miny, maxx, maxy = inner.bounds
    pitch = max(float(pitch), 0.02)
    chains, chain, y, k = [], None, miny, 0
    while y <= maxy + 1e-9:
        seg = LineString([(minx - 1.0, y), (maxx + 1.0, y)]).intersection(inner)
        if seg.geom_type == 'LineString':
            parts = [seg]
        elif seg.geom_type == 'MultiLineString':
            parts = list(seg.geoms)
        else:
            parts = []
        oriented = []
        for part in parts:
            c = list(part.coords)
            if len(c) < 2:
                continue
            if k % 2 == 1:
                c.reverse()
            oriented.append(c)
        while oriented:
            if chain is not None:
                # Une con la pieza de esta fila mas cercana a la cadena, si el
                # tramo vertical cabe dentro del cobre.
                best = None
                for idx, c in enumerate(oriented):
                    link = LineString([chain[-1], c[0]])
                    if link.length <= pitch * 1.5 and inner.buffer(1e-6).contains(link):
                        if best is None or link.length < best[0]:
                            best = (link.length, idx)
                if best is not None:
                    chain.extend(oriented.pop(best[1]))
                    continue
                chains.append(chain)
                chain = None
            chain = list(oriented.pop(0))
        if not parts and chain is not None:
            chains.append(chain)
            chain = None
        y += pitch
        k += 1
    if chain is not None:
        chains.append(chain)
    return chains


def gerber_to_paths(gerber_text, tool_diameter=0.2, clearance=0.15,
                    simplify_tol=0.02, mirror_x=False, mirror_y=False, rotation=0,
                    modo="contorno", hatch_pitch=0.15, incluir_plano=False,
                    edge_gerber=None):
    parser = GerberParser()
    primitives = parser.parse(gerber_text)

    if not primitives:
        return [], None, {"error": "Sin primitivas"}

    geoms = []
    for p in primitives:
        g = _primitive_to_shapely(p)
        if g is not None and not g.is_empty:
            geoms.append(g)

    # Modo aislamiento: el plano ES el cobre; las pistas y pads se sustraen de el
    # para saber donde NO hay material, y se fresa el margen entre ambos.
    if modo == "aislamiento" and parser.regions:
        from shapely.ops import unary_union as _uu
        plane = _uu(parser.regions)
        tracks = _uu(geoms) if geoms else plane
        # El cobre a aislar es el plano menos el interior de las pistas/pads
        # mas la propia pista (hay que conservar su borde).
        geoms = [plane]
    elif incluir_plano:
        geoms.extend(parser.regions)
    if not geoms:
        return [], parser.bounds, {"error": "Sin geometrias validas"}

    copper = unary_union(geoms)

    # Transformacion del cobre antes del buffer: espejo y rotacion alrededor
    # del origen. El offset posterior se calcula con los bounds ya transformados.
    if mirror_x or mirror_y or rotation:
        from shapely import affinity
        if mirror_x or mirror_y:
            copper = affinity.scale(copper, xfact=-1.0 if mirror_x else 1.0,
                                    yfact=-1.0 if mirror_y else 1.0, origin=(0, 0))
        if rotation:
            # Positivo = antihorario, igual que en el visor.
            copper = affinity.rotate(copper, float(rotation), origin=(0, 0))

    offset = tool_diameter / 2.0 + clearance
    isolation = copper.buffer(offset, join_style=1, quad_segs=8)

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

    if modo in ("relleno", "ambos"):
        fill = _hatch_paths(copper, tool_diameter, hatch_pitch)
        paths = fill if modo == "relleno" else paths + fill

    if edge_gerber and not getattr(gerber_to_paths, "_in_edge", False):
        try:
            gerber_to_paths._in_edge = True
            ep, _, _ = gerber_to_paths(edge_gerber, tool_diameter=tool_diameter,
                                        clearance=0.0, simplify_tol=simplify_tol,
                                        mirror_x=mirror_x, mirror_y=mirror_y,
                                        rotation=rotation, modo="contorno",
                                        incluir_plano=False)
            paths = paths + ep
        except Exception as _e:
            pass
        finally:
            gerber_to_paths._in_edge = False
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


def _circle_3pt(a, b, c):
    """Centro de la circunferencia que pasa por tres puntos (None si son colineales)."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy


def _path_moves(path, min_seg=0.05, arc_fit=True, arc_tol=0.015,
                max_span=math.pi, max_window=40, max_radius=10.0):
    """Convierte un trazado en movimientos: ('L', x, y) o ('A', x, y, I, J, ccw).

    1) Quita puntos separados menos de min_seg (micro-segmentos de las esquinas).
    2) Si arc_fit, agrupa tramos que siguen una circunferencia (error <= arc_tol)
       y los emite como G2/G3 con centro relativo (I, J) al punto inicial.
    Devuelve (movimientos, longitud_total_mm)."""
    # Vertices reducidos (min_seg) y, para cada uno, su indice en el trazado original.
    pts = [path[0]]
    idx = [0]
    for k in range(1, len(path)):
        p = path[k]
        if math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) >= min_seg:
            pts.append(p)
            idx.append(k)
    last = path[-1]
    if pts[-1] != last:
        if len(pts) > 1 and math.hypot(last[0] - pts[-1][0], last[1] - pts[-1][1]) < min_seg:
            pts[-1] = last
            idx[-1] = len(path) - 1
        else:
            pts.append(last)
            idx.append(len(path) - 1)

    moves = []
    dist = 0.0
    n = len(pts)
    i = 0
    while i < n - 1:
        best = None
        if arc_fit:
            j = i + 2
            while j < n and j - i <= max_window:
                mid = pts[(i + j) // 2]
                c = _circle_3pt(pts[i], mid, pts[j])
                if c is None:
                    break
                cx, cy = c
                r = math.hypot(pts[i][0] - cx, pts[i][1] - cy)
                if r > max_radius:
                    break
                # Validacion densa: TODOS los puntos originales entre i y j deben
                # quedar a arc_tol del arco, no solo los vertices reducidos.
                if not all(abs(math.hypot(path[k][0] - cx, path[k][1] - cy) - r) <= arc_tol
                           for k in range(idx[i], idx[j] + 1)):
                    break
                a0 = math.atan2(pts[i][1] - cy, pts[i][0] - cx)
                am = math.atan2(mid[1] - cy, mid[0] - cx)
                a1 = math.atan2(pts[j][1] - cy, pts[j][0] - cx)
                ccw_sweep = (a1 - a0) % (2.0 * math.pi)
                ccw = ((am - a0) % (2.0 * math.pi)) < ccw_sweep
                sweep = ccw_sweep if ccw else 2.0 * math.pi - ccw_sweep
                if sweep > max_span:
                    break
                best = (j, cx, cy, ccw)
                j += 1
        if best is not None:
            j, cx, cy, ccw = best
            sx, sy = pts[i]
            moves.append(("A", pts[j][0], pts[j][1], cx - sx, cy - sy, ccw))
            for k in range(i, j):
                dist += math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1])
            i = j
        else:
            p1 = pts[i + 1]
            moves.append(("L", p1[0], p1[1]))
            dist += math.hypot(p1[0] - pts[i][0], p1[1] - pts[i][1])
            i += 1
    return moves, dist


def gerber_to_gcode(gerber_text, tool_diameter=0.2, clearance=0.15,
                    feed=300.0, rapid=3000.0, pen_up=2.0, pen_down=0.0,
                    origin_x=0.0, origin_y=0.0, simplify_tol=0.02,
                    mirror_x=False, mirror_y=False, rotation=0,
                    work_w=None, work_h=None,
                    pen_mode="m280", servo_up=0.0, servo_down=95.0, servo_p0=True,
                    min_seg=0.05, arc_fit=True, arc_tol=0.015, servo_down_speed=None,
                    modo="contorno", hatch_pitch=0.15, edge_gerber=None,
                    ref_bounds=None):
    paths, bounds, info = gerber_to_paths(gerber_text, tool_diameter, clearance,
                                          simplify_tol=simplify_tol,
                                          mirror_x=mirror_x, mirror_y=mirror_y,
                                          rotation=rotation, modo=modo if modo != "aislamiento" else "contorno",
                                          hatch_pitch=hatch_pitch,
                                          incluir_plano=(modo == "aislamiento"),
                                          edge_gerber=edge_gerber)

    if not paths:
        raise ValueError("No se pudieron extraer paths del Gerber")

    # Pluma: "m280" emite M280 explicitos (valores elegidos en la GUI);
    # "plotter" usa M301/M302, que en el firmware sube/baja con angulos fijos.
    def servo_cmd(angle, speed=None):
        base = f"M280 P0 S{angle:g}" if servo_p0 else f"M280 S{angle:g}"
        return base if speed is None else f"{base} F{speed:g}"

    offset_x = origin_x
    offset_y = origin_y
    if bounds:
        minx, miny, _, _ = bounds
        # Marco comun de la placa: si se pasa ref_bounds, la capa se alinea con
        # esa referencia (p.ej. el contorno Edge.Cuts) en vez de con su propia caja.
        if ref_bounds is not None:
            minx, miny = ref_bounds[0], ref_bounds[1]
        offset_x = origin_x - minx
        offset_y = origin_y - miny

    # Verificacion de area de trabajo: el PCB debe caber en 0..work_w, 0..work_h.
    fit_warn = []
    if work_w or work_h:
        xs = [pt[0] + offset_x for path in paths for pt in path]
        ys = [pt[1] + offset_y for path in paths for pt in path]
        if work_w and (min(xs) < 0 or max(xs) > work_w):
            fit_warn.append(f"PCB fuera del area en X: {min(xs):.1f}..{max(xs):.1f} mm (area 0..{work_w:.1f})")
        if work_h and (min(ys) < 0 or max(ys) > work_h):
            fit_warn.append(f"PCB fuera del area en Y: {min(ys):.1f}..{max(ys):.1f} mm (area 0..{work_h:.1f})")
    info = dict(info)
    info["warnings"] = list(info.get("warnings") or []) + fit_warn

    lines = []
    lines.append("; CNC IDE -- Gerber a G-code (shapely)")
    lines.append("; =====================================")
    lines.append(f"; Primitivas: {info['primitives']}")
    lines.append(f"; Paths: {len(paths)}")
    lines.append(f"; Tool: {tool_diameter:.2f} mm, clearance: {clearance:.2f} mm")
    lines.append(f"; Espejo X: {bool(mirror_x)}, Espejo Y: {bool(mirror_y)}, Rotacion: {rotation} deg")
    for w in fit_warn:
        lines.append(f"; AVISO: {w}")
    lines.append("G21")
    lines.append("G90")
    if pen_mode == "plotter":
        lines.append("M301")
    else:
        lines.append(f"; Pluma M280: arriba S{servo_up:g}, abajo S{servo_down:g}")
        lines.append(servo_cmd(servo_up))

    total_dist = 0.0

    for path in paths:
        if len(path) < 2:
            continue
        x0, y0 = path[0]
        moves, plen = _path_moves(path, min_seg=min_seg, arc_fit=arc_fit, arc_tol=arc_tol)
        if not moves or plen < min_seg:
            continue  # trazo sin longitud util: no bajar ni subir la pluma
        lines.append(f"G1 X{x0 + offset_x:.3f} Y{y0 + offset_y:.3f} F{feed:.1f}")
        if pen_mode != "plotter":
            lines.append(servo_cmd(servo_down, speed=servo_down_speed))
        total_dist += plen
        for mv in moves:
            if mv[0] == "L":
                lines.append(f"G1 X{mv[1] + offset_x:.3f} Y{mv[2] + offset_y:.3f} F{feed:.1f}")
            else:
                _, ex, ey, di, dj, ccw = mv
                code = "G3" if ccw else "G2"
                lines.append(f"{code} X{ex + offset_x:.3f} Y{ey + offset_y:.3f} "
                             f"I{di:.3f} J{dj:.3f} F{feed:.1f}")
        if pen_mode != "plotter":
            lines.append(servo_cmd(servo_up))

    lines.append(f"G1 X{origin_x:.3f} Y{origin_y:.3f} F{feed:.1f}")
    if pen_mode == "plotter":
        lines.append("M302")
    lines.append("M30")
    lines.append(f"; Distancia total: {total_dist:.1f} mm")

    return lines, info
