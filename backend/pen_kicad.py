"""Modo 'plumon_kicad': dibuja el cobre del Gerber como lo muestra KiCad.

Regla unica, pensada para un plumon de diametro `tool_diameter` (0.5 mm):
  * pista de ancho <= plumon        -> UNA pasada por el centro.
  * pista de ancho mayor            -> pasadas paralelas que cubren el ancho
                                       exacto (1 mm = 2 pasadas, 2 mm = 4).
  * pads                            -> su forma real, rellena con anillos
                                       concentricos y el taladro libre.
  * zonas rellenas                  -> anillos concentricos.
Todo trazo queda DENTRO del cobre, asi que la separacion entre redes distintas
es la que dejo KiCad (clearance). `verify` lo comprueba numericamente.
"""
import math

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

from gerber_parser import (GerberParser, _arc_points, _load_drill_holes,
                           _primitive_to_shapely, _transform_holes)


def _union(geoms):
    """Union tolerante a TopologyException."""
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return LineString()
    try:
        return unary_union(geoms)
    except Exception:
        pass
    try:
        return shapely.union_all(geoms, grid_size=1e-3)
    except Exception:
        return unary_union([g.buffer(0) for g in geoms])


def _diff(a, b):
    """a - b tolerante a TopologyException (offsets casi coincidentes)."""
    if a is None or b is None or a.is_empty or b.is_empty:
        return a
    try:
        return a.difference(b)
    except Exception:
        pass
    try:
        return shapely.difference(a, b, grid_size=1e-3)
    except Exception:
        return a.buffer(0).difference(b.buffer(0))


def _polys(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == 'Polygon':
        return [g]
    out = []
    for q in getattr(g, 'geoms', []):
        out.extend(_polys(q))
    return out


def _lines(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == 'LineString':
        return [g]
    out = []
    for q in getattr(g, 'geoms', []):
        out.extend(_lines(q))
    return out


def _rings(poly):
    return [list(poly.exterior.coords)] + [list(r.coords) for r in poly.interiors]


def _mic(poly):
    try:
        return float(shapely.maximum_inscribed_circle(poly, tolerance=0.02).length)
    except Exception:
        return 0.0


def _add_ring(strokes, r, limit):
    """Anade un anillo; si hay `limit` (cobre erosionado) se recorta para que
    la banda no invada la separacion con el cobre vecino."""
    if limit is None:
        strokes.append(("ring", r))
        return
    ls = LineString(r)
    try:
        if limit.contains(ls):
            strokes.append(("ring", r))
            return
        part = ls.intersection(limit)
    except Exception:
        return
    for q in _lines(part):
        c = list(q.coords)
        if len(c) >= 2 and q.length >= 0.05:
            strokes.append(("line", c))


def _fill_poly(poly, pen, step, strokes, level=0, sliver=0.08,
               min_area=0.05, limit=None):
    """Rellena un poligono con anillos concentricos. El primer anillo queda a
    pen/2 del borde (el borde de la banda coincide con el borde del cobre).
    Lo que no cubren los anillos se trata en `_fill_residual`."""
    if poly.is_empty or poly.area < min_area:
        return
    t = _mic(poly)
    if t <= 0.0:
        return
    first = min(pen / 2.0, t * 0.98)
    bands = []
    k = 0
    while True:
        d = first + k * step
        if d >= t * 0.999:
            break
        R = poly.buffer(-d, resolution=16)
        if R.is_empty and k == 0:
            for f in (0.9, 0.75, 0.55, 0.35, 0.15):
                R = poly.buffer(-d * f, resolution=16)
                if not R.is_empty:
                    break
        if R.is_empty:
            break
        for q in _polys(R):
            for r in _rings(q):
                if len(r) >= 4:
                    if level == 0:
                        strokes.append(("ring", r))
                    else:
                        _add_ring(strokes, r, limit)
                    bands.append(LineString(r).buffer(pen / 2.0, cap_style=1))
        k += 1
    if level >= 3 or not bands:
        return
    left = _diff(poly, _union(bands))
    if left.is_empty:
        return
    left = left.buffer(-sliver).buffer(sliver)
    for q in _polys(left):
        if q.area >= min_area:
            _fill_residual(q, pen, step, strokes, level + 1, sliver, min_area, limit)


def _fill_residual(q, pen, step, strokes, level, sliver, min_area, limit):
    """Restos que dejaron los anillos: donde cabe el plumon se rellena con
    anillos; las tiras mas estrechas que el plumon llevan una pasada central."""
    r = pen / 2.0 * 0.95
    fat = q.buffer(-r).buffer(r)
    for f in _polys(fat):
        if f.area >= min_area:
            _fill_poly(f, pen, step, strokes, level, sliver, min_area, limit)
    thin = _diff(q, fat).buffer(-sliver * 0.75).buffer(sliver * 0.75)
    for tp in _polys(thin):
        if tp.area < min_area:
            continue
        t = _mic(tp)
        if t <= 0.0:
            continue
        R = tp.buffer(-0.5 * t, resolution=8)
        if R.is_empty:
            R = tp.buffer(-0.3 * t, resolution=8)
        for rr in _polys(R):
            for ring in _rings(rr):
                if len(ring) >= 4:
                    _add_ring(strokes, ring, limit)


def _order(items, cover, link_max=0.9, start=(0.0, 0.0)):
    """Orden vecino-mas-cercano. Los anillos se empiezan por el vertice mas
    cercano; las lineas se pueden recorrer al reves. Si el siguiente trazo
    empieza a <= link_max y el tramo de union va por dentro del cobre, se
    encadena sin levantar la pluma."""
    n = len(items)
    if n == 0:
        return []
    pts, owner, cidx = [], [], []
    for i, (kind, p) in enumerate(items):
        if kind == "ring":
            m = len(p) - 1
            st = max(1, m // 24)
            idxs = list(range(0, m, st))
        else:
            idxs = [0, len(p) - 1]
        for j in idxs:
            pts.append(p[j]); owner.append(i); cidx.append(j)
    P = np.array(pts, dtype=float)
    owner = np.array(owner)
    cidx = np.array(cidx)
    alive = np.ones(len(P), dtype=bool)
    pcover = prep(cover) if cover is not None else None
    out, cur = [], None
    pos = np.array(start, dtype=float)
    for _ in range(n):
        d = np.hypot(P[:, 0] - pos[0], P[:, 1] - pos[1])
        d[~alive] = np.inf
        c = int(np.argmin(d))
        i, j = int(owner[c]), int(cidx[c])
        kind, p = items[i]
        if kind == "ring":
            q = p[j:-1] + p[:j] + [p[j]]
        else:
            q = list(p) if j == 0 else list(p[::-1])
        alive[owner == i] = False
        if (cur is not None and d[c] <= link_max and pcover is not None
                and pcover.contains(LineString([cur[-1], q[0]]))):
            cur.extend(q)
        else:
            if cur is not None:
                out.append(cur)
            cur = list(q)
        pos = np.array(cur[-1], dtype=float)
    out.append(cur)
    return out


def verify_strokes(paths, copper_all, pen, gap_warn=0.15, copper_solid=None):
    """Comprueba cobertura, tinta fuera del cobre y distancia entre trazos de
    redes (islas de cobre) distintas."""
    bands = [LineString(p).buffer(pen / 2.0, cap_style=1) for p in paths if len(p) >= 2]
    band_all = _union(bands)
    copper_area = copper_all.area
    outside = _diff(band_all, (copper_solid if copper_solid is not None else copper_all).buffer(0.03)).area
    unc = _diff(copper_all, band_all).buffer(-0.05).buffer(0.05)
    comps = _polys(copper_all)
    tree = STRtree(comps)
    by_comp = {}
    for b in bands:
        idx = tree.query(b.representative_point(), predicate="intersects")
        if len(idx):
            by_comp.setdefault(int(idx[0]), []).append(b)
    comp_bands = {i: _union(v) for i, v in by_comp.items()}
    keys = list(comp_bands.keys())
    btree = STRtree([comp_bands[k] for k in keys])
    min_gap, close = float("inf"), []
    for a, ka in enumerate(keys):
        for bi in btree.query(comp_bands[ka].buffer(1.0)):
            if bi <= a:
                continue
            g = comp_bands[ka].distance(comp_bands[keys[int(bi)]])
            if g < min_gap:
                min_gap = g
            if g < gap_warn:
                pa, pb = nearest_points(comp_bands[ka], comp_bands[keys[int(bi)]])
                close.append((round(g, 3), (round(pa.x, 2), round(pa.y, 2))))
    cmin = float("inf")
    ctree = STRtree(comps)
    for a, ca in enumerate(comps):
        for bi in ctree.query(ca.buffer(2.0)):
            if bi > a:
                cmin = min(cmin, ca.distance(comps[int(bi)]))
    return {
        "cobre_mm2": round(copper_area, 1),
        "sin_cubrir_mm2": round(unc.area, 2),
        "tinta_fuera_mm2": round(outside, 2),
        "islas_cobre": len(comps),
        "separacion_min_cobre_mm": round(cmin, 3) if cmin != float("inf") else None,
        "separacion_min_trazos_mm": round(min_gap, 3) if min_gap != float("inf") else None,
        "pares_cerca": sorted(close)[:8],
    }


def _parallel_passes_line(x1, y1, x2, y2, width, pen, ext):
    """n = round(width/pen) pasadas paralelas, centradas, que cubren el ancho
    exacto de la pista. Se extienden `ext` mas alla de los extremos para que
    conecten de verdad con el pad o la pista siguiente (se recorta despues
    contra el cobre real)."""
    L = math.hypot(x2 - x1, y2 - y1)
    if L < 1e-6:
        return []
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    nx, ny = -uy, ux
    ex1, ey1 = x1 - ux * ext, y1 - uy * ext
    ex2, ey2 = x2 + ux * ext, y2 + uy * ext
    n = max(1, round(width / pen))
    spacing = width / n
    first_off = -width / 2.0 + spacing / 2.0
    lines = []
    for k in range(n):
        o = first_off + k * spacing
        lines.append(LineString([(ex1 + nx * o, ey1 + ny * o),
                                 (ex2 + nx * o, ey2 + ny * o)]))
    return lines


def _parallel_passes_arc(x1, y1, x2, y2, cx, cy, cw, width, pen, ext):
    """Igual que _parallel_passes_line pero para arcos: pasadas concentricas
    con el mismo centro, a radios separados `width/n`."""
    r = math.hypot(x1 - cx, y1 - cy)
    a1 = math.atan2(y1 - cy, x1 - cx)
    a2 = math.atan2(y2 - cy, x2 - cx)
    if cw:
        while a2 >= a1:
            a2 -= 2 * math.pi
    else:
        while a2 <= a1:
            a2 += 2 * math.pi
    direction = 1.0 if a2 > a1 else -1.0
    d_ext = (ext / max(r, 0.05)) * direction
    a1e, a2e = a1 - d_ext, a2 + d_ext
    n = max(1, round(width / pen))
    spacing = width / n
    first_off = -width / 2.0 + spacing / 2.0
    lines = []
    segments = max(6, int(abs(a2e - a1e) / 0.15))
    for k in range(n):
        o = first_off + k * spacing
        rr = r + o
        if rr <= 0.01:
            continue
        pts = []
        for i in range(segments + 1):
            t = i / segments
            a = a1e + (a2e - a1e) * t
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        lines.append(LineString(pts))
    return lines


def gerber_plumon_kicad(gerber_text, tool_diameter=0.5, mirror_x=False,
                        mirror_y=False, rotation=0, drill_text=None,
                        hole_margin=0.0, overlap=0.0, edge_margin=0.0,
                        gap_warn=0.15, simplify_tol=0.01, link_max=0.9,
                        verify=True, **_unused):
    pen = float(tool_diameter)
    step = pen * (1.0 - max(0.0, min(0.5, float(overlap))))
    parser = GerberParser()
    prims = parser.parse(gerber_text)
    if not prims:
        return [], None, {"error": "Sin primitivas"}

    def tf(g):
        if g is None or g.is_empty:
            return g
        if mirror_x or mirror_y:
            g = affinity.scale(g, xfact=-1.0 if mirror_x else 1.0,
                               yfact=-1.0 if mirror_y else 1.0, origin=(0, 0))
        if rotation:
            g = affinity.rotate(g, float(rotation), origin=(0, 0))
        return g

    thin, area_geoms, all_geoms, wide_prims = [], [], [], []
    n_thin_narrow = 0
    for p in prims:
        if p.kind in ('line', 'arc') and p.width > 0:
            g = _primitive_to_shapely(p)
            if g is None or g.is_empty:
                continue
            all_geoms.append(g)
            if p.width <= pen * 1.05:
                if p.width < pen * 0.95:
                    n_thin_narrow += 1
                if p.kind == 'line':
                    thin.append(LineString([(p.x1, p.y1), (p.x2, p.y2)]))
                else:
                    thin.append(LineString(_arc_points(p.x1, p.y1, p.x2, p.y2,
                                                       p.cx, p.cy, p.cw, segments=32)))
            else:
                wide_prims.append(p)
        elif p.kind == 'flash':
            g = _primitive_to_shapely(p)
            if g is not None and not g.is_empty:
                area_geoms.append(g)
                all_geoms.append(g)
    for r in parser.regions:
        if r is not None and not r.is_empty:
            area_geoms.append(r)
            all_geoms.append(r)
    if not all_geoms:
        return [], parser.bounds, {"error": "Sin geometrias validas"}

    copper_all = tf(unary_union(all_geoms))
    holes_t = _transform_holes(_load_drill_holes(drill_text), mirror_x, mirror_y, rotation)
    hole_zone = (unary_union([Point(x, y).buffer(d / 2.0 + float(hole_margin), resolution=16)
                              for x, y, d in holes_t]) if holes_t else None)

    # El area se rellena SOLIDA (sin restar taladros): el primer anillo queda a
    # pen/2 del borde exterior del pad. Los taladros se recortan despues.
    A = tf(unary_union(area_geoms)) if area_geoms else None
    Aeff = A.buffer(-float(edge_margin)) if (A is not None and edge_margin > 0) else A

    strokes = []
    limit_cu = copper_all.buffer(-(pen / 2.0 - 0.05))
    if Aeff is not None and not Aeff.is_empty:
        for poly in _polys(Aeff):
            _fill_poly(poly, pen, step, strokes, limit=limit_cu)
    n_rings = len(strokes)

    # Pistas de 1 sola pasada: lo que ya cubre el relleno no se repite.
    keep = A.buffer(-0.05) if (A is not None and not A.is_empty) else None
    for ls in thin:
        g = tf(ls)
        if hole_zone is not None:
            g = _diff(g, hole_zone)
        if keep is not None:
            g = _diff(g, keep)
        for q in _lines(g):
            c = list(q.coords)
            if len(c) >= 2 and q.length >= 0.02:
                strokes.append(("line", c))
    # Pistas anchas (>= plumon): N = round(ancho/pen) pasadas paralelas que
    # cubren el ancho exacto, extendidas y recortadas contra el cobre real
    # para que conecten limpio con pads y zonas en los extremos.
    ext = pen / 2.0 + 0.05
    for p in wide_prims:
        if p.kind == 'line':
            segs = _parallel_passes_line(p.x1, p.y1, p.x2, p.y2, p.width, pen, ext)
        else:
            segs = _parallel_passes_arc(p.x1, p.y1, p.x2, p.y2, p.cx, p.cy, p.cw,
                                        p.width, pen, ext)
        for ls in segs:
            # Recorte contra limit_cu (cobre erosionado pen/2-0.05): igual
            # margen de seguridad que usan los anillos, asi el extremo cortado
            # no deja que el circulo de la pluma (radio pen/2) se salga del cobre.
            g = tf(ls).intersection(limit_cu)
            for q in _lines(g):
                c = list(q.coords)
                if len(c) >= 2 and q.length >= 0.02:
                    strokes.append(("line", c))

    if hole_zone is not None:
        clipped = []
        for kind, c in strokes:
            ls = LineString(c)
            if not ls.intersects(hole_zone):
                clipped.append((kind, c))
                continue
            for q in _lines(_diff(ls, hole_zone)):
                cc = list(q.coords)
                if len(cc) >= 2 and q.length >= 0.02:
                    clipped.append(("line", cc))
        strokes = clipped
    n_rings = sum(1 for k, _ in strokes if k == "ring")
    n_lines = len(strokes) - n_rings
    copper_eff = _diff(copper_all, hole_zone) if hole_zone is not None else copper_all

    items = []
    for kind, c in strokes:
        ls = LineString(c).simplify(float(simplify_tol), preserve_topology=False)
        cc = list(ls.coords)
        if kind == "ring":
            if len(cc) < 4:
                continue
            cc[-1] = cc[0]
        elif len(cc) < 2:
            continue
        items.append((kind, cc))
    paths = _order(items, copper_eff.buffer(-(pen / 2.0 - 0.03)), link_max=float(link_max))
    if not paths:
        return [], parser.bounds, {"error": "Sin trazos"}

    xs = [pt[0] for pth in paths for pt in pth]
    ys = [pt[1] for pth in paths for pt in pth]
    bounds = (min(xs), min(ys), max(xs), max(ys))
    warnings = list(parser.warnings)
    if n_thin_narrow:
        warnings.append(f"{n_thin_narrow} pistas mas finas que el plumon ({pen:g} mm): se dibujan con 1 pasada.")
    info = {"primitives": len(prims), "paths": len(paths), "thin": n_lines,
            "pads": sum(1 for p in prims if p.kind == 'flash'),
            "anillos": n_rings, "warnings": warnings, "bounds": bounds}
    if verify:
        info["verif"] = verify_strokes(paths, copper_eff, pen, gap_warn, copper_solid=copper_all)
    return paths, bounds, info
