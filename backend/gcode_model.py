"""Analizador comun de trayectorias CNC en milimetros.

Los segmentos conservan los seis campos historicos y agregan Z inicial/final.
Los arcos se discretizan para que vista y simulador recorran la misma curva.
"""
import math
import re

WORD = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


def _clean(raw):
    return re.sub(r"\([^)]*\)", "", str(raw).split(";")[0]).upper().strip()


def parse_program(lines, arc_tolerance=0.02):
    x = y = z = 0.0
    unit = 1.0
    absolute = True
    motion = None
    feed = 0.0
    spindle = False
    segments = []
    warnings = []
    stats = {"total": len(lines), "empty": 0, "comments": 0,
             "g0": 0, "g1": 0, "g2": 0, "g3": 0, "other_g": 0,
             "m_codes": 0, "min_x": None, "max_x": None,
             "min_y": None, "max_y": None, "distance": 0.0,
             "error_lines": []}
    for idx, raw in enumerate(lines):
        line = _clean(raw)
        if not line:
            stats["empty"] += 1
            continue
        words = [(m.group(1), float(m.group(2))) for m in WORD.finditer(line)]
        gs = [v for k, v in words if k == "G"]
        ms = [int(v) for k, v in words if k == "M"]
        supported_g = {0, 1, 2, 3, 17, 18, 19, 20, 21, 28, 53, 90, 91, 92, 93, 94, 95,
                       54, 55, 56, 57, 58, 59}
        for g in gs:
            if g not in supported_g:
                warnings.append({"line": idx + 1, "code": "unsupported_g", "text": f"G{g:g} no está en el perfil conocido del STM32"})
        vals = {}
        for k, v in words:
            vals[k] = v
        if 20 in gs: unit = 25.4
        if 21 in gs: unit = 1.0
        if 90 in gs: absolute = True
        if 91 in gs: absolute = False
        for m in ms:
            stats["m_codes"] += 1
            if m in (3, 4): spindle = True
            if m == 5: spindle = False
        if "F" in vals: feed = vals["F"] * unit
        specified = next((int(g) for g in gs if g in (0, 1, 2, 3)), None)
        if specified is not None:
            motion = specified
        elif any(k in vals for k in "XYZ"):
            warnings.append({"line": idx + 1, "code": "modal_motion", "text": "Movimiento modal; el firmware actual requiere G0/G1/G2/G3 explícito"})
            if motion is None:
                continue
        if motion not in (0, 1, 2, 3) or not any(k in vals for k in "XYZ"):
            for g in gs:
                if g not in (0, 1, 2, 3, 17, 20, 21, 90, 91, 94): stats["other_g"] += 1
            continue
        key = f"g{motion}"
        stats[key] += 1
        start = (x, y, z)
        end = tuple(((vals[a] * unit if absolute else p + vals[a] * unit) if a in vals else p)
                    for a, p in zip("XYZ", start))
        points = [end]
        if motion in (2, 3):
            can_arc = True
            if "R" in vals and not ("I" in vals or "J" in vals):
                radius = abs(vals["R"] * unit)
                chord = math.hypot(end[0]-x, end[1]-y)
                if chord < 1e-8 or chord > 2*radius:
                    warnings.append({"line": idx+1, "code": "arc_invalid", "text": "Radio R inválido; se muestra como recta"})
                    can_arc = False
                else:
                    mx, my = (x+end[0])/2, (y+end[1])/2
                    h = math.sqrt(max(0.0, radius*radius-chord*chord/4))
                    nx, ny = -(end[1]-y)/chord, (end[0]-x)/chord
                    centers = [(mx+nx*h,my+ny*h),(mx-nx*h,my-ny*h)]
                    def sweep_for(center):
                        aa=math.atan2(y-center[1],x-center[0]); ab=math.atan2(end[1]-center[1],end[0]-center[0])
                        return ((ab-aa)%(2*math.pi)) if motion == 3 else -((aa-ab)%(2*math.pi))
                    candidates=[(c,sweep_for(c)) for c in centers]
                    want_major=vals["R"]<0
                    chosen=next((cs for cs in candidates if (abs(cs[1]) > math.pi) == want_major), candidates[0])
                    cx,cy=chosen[0]
            else:
                i = vals.get("I", 0.0) * unit
                j = vals.get("J", 0.0) * unit
                cx, cy = (i, j) if not absolute else (x + i, y + j)
            r0, r1 = (math.hypot(x-cx, y-cy), math.hypot(end[0]-cx, end[1]-cy)) if can_arc else (0, 0)
            if not can_arc:
                pass
            elif r0 < 1e-8 or abs(r0-r1) > max(0.5, r0*0.02):
                warnings.append({"line": idx+1, "code": "arc_invalid", "text": "Arco I/J inválido; se muestra como recta"})
            else:
                a0, a1 = math.atan2(y-cy, x-cx), math.atan2(end[1]-cy, end[0]-cx)
                sweep = ((a1-a0) % (2*math.pi)) if motion == 3 else -((a0-a1) % (2*math.pi))
                if abs(sweep) < 1e-9: sweep = 2*math.pi if motion == 3 else -2*math.pi
                # step angular segun arc_tolerance (antes estaba fijo en 0.1 mm)
                chord_error = max(0.005, float(arc_tolerance))
                step = max(0.01, 2*math.asin(chord_error / (2*max(r0, 0.1))))
                n = min(4000, max(2, math.ceil(abs(sweep)/step)))
                # muestreo con easing coseno para joins suaves
                pts = [
                    (
                        cx + r0*math.cos(a0 + sweep*(1 - math.cos(math.pi*t/n))/2),
                        cy + r0*math.sin(a0 + sweep*(1 - math.cos(math.pi*t/n))/2),
                        z + (end[2]-z)*t/n
                    )
                    for t in range(1, n+1)
                ]
                # forzamos el punto final exactamente sobre el objetivo
                pts[-1] = (end[0], end[1], end[2])
                points = pts
        prev = start
        kind = "rapid" if motion == 0 else ("arc" if motion in (2,3) else "cut")
        for p in points:
            segments.append((prev[0], prev[1], p[0], p[1], kind, idx, prev[2], p[2], feed, spindle))
            stats["distance"] += math.dist(prev, p)
            for axis, value in (("x", p[0]), ("y", p[1])):
                stats[f"min_{axis}"] = value if stats[f"min_{axis}"] is None else min(stats[f"min_{axis}"], value)
                stats[f"max_{axis}"] = value if stats[f"max_{axis}"] is None else max(stats[f"max_{axis}"], value)
            prev = p
        x, y, z = end
    return {"segments": segments, "stats": stats, "warnings": warnings, "end": (x,y,z)}