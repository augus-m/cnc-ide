"""Simplificacion de trayectorias G-code con tolerancia de error.

Para cada tramo de G1 con X/Y en modo absoluto:
  1. Douglas-Peucker: quita puntos cuya distancia a la recta entre los
     puntos conservados es <= tol (el error maximo queda acotado por tol).
  2. Los puntos originales estan sobre la curva; por eso el resultado
     tambien se mantiene dentro de tol de la curva.

Referencia de longitud maxima por curvatura (solo informativa):
  error de cuerda e = L^2 / (8 R)  ->  L_max = sqrt(8 R e)

No cambia lineas de Z, G0 ni comandos G2/G3; esas actuan como fronteras.
"""
import math
import re

_TOKEN = re.compile(r"([XYF])(-?\d+(?:\.\d+)?)", re.IGNORECASE)


def _tokens(line):
    vals = {}
    for k, v in _TOKEN.findall(line):
        vals[k.upper()] = float(v)
    return vals


def _is_g(line, code):
    t = line.strip().upper()
    return t.startswith(f"G{code}") and (len(t) == len(f"G{code}") or not t[len(f"G{code}")].isdigit())


def _dp(points, tol):
    """Douglas-Peucker sobre una lista de (x, y). Conserva los extremos."""
    n = len(points)
    if n <= 2:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        worst, widx = 0.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            if seg < 1e-9:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dx * (py - ay) - dy * (px - ax)) / seg
            if d > worst:
                worst, widx = d, i
        if widx >= 0 and worst > tol:
            keep[widx] = True
            stack.append((a, widx))
            stack.append((widx, b))
    return [p for p, k in zip(points, keep) if k]


def optimize_lines(lines, tol=0.01):
    """Devuelve (lineas_nuevas, estadisticas). Si el archivo usa G91
    (relativo) no se optimiza y se devuelve el original."""
    if any(_is_g(ln, 91) for ln in lines):
        return list(lines), None

    out = []
    pos = None          # ultima posicion conocida (x, y)
    feed = None         # F modal
    run = []            # [(x, y)] de la corrida actual
    run_feed = None
    run_start = None    # punto inicial de la corrida (ya ejecutado)
    g1_before = 0

    def flush():
        nonlocal run, run_feed, run_start
        if run and run_start is not None:
            pts = [run_start] + run
            kept = _dp(pts, tol)
            for (x, y) in kept[1:]:
                f_txt = f" F{run_feed:.1f}" if run_feed is not None else ""
                out.append(f"G1 X{x:.3f} Y{y:.3f}{f_txt}")
        run = []
        run_feed = None
        run_start = None

    for ln in lines:
        t = ln.strip()
        if _is_g(t, 1) and ("X" in t.upper() or "Y" in t.upper()):
            g1_before += 1
            v = _tokens(t)
            x = v.get("X", pos[0] if pos else None)
            y = v.get("Y", pos[1] if pos else None)
            if "F" in v:
                feed = v["F"]
            if x is None or y is None or feed is None:
                flush()
                out.append(ln)
                pos = (x, y) if (x is not None and y is not None) else pos
                continue
            if run and feed != run_feed:
                flush()
            if not run:
                if pos is None:
                    out.append(ln)
                    pos = (x, y)
                    continue
                run_start = pos
                run_feed = feed
            run.append((x, y))
            pos = (x, y)
        else:
            flush()
            out.append(ln)
            if _is_g(t, 0) or _is_g(t, 28) or _is_g(t, 92):
                v = _tokens(t)
                if "X" in v or "Y" in v:
                    pos = (v.get("X", pos[0] if pos else 0.0),
                           v.get("Y", pos[1] if pos else 0.0))
    flush()

    g1_after = sum(1 for ln in out if _is_g(ln.strip(), 1) and ("X" in ln.upper() or "Y" in ln.upper()))
    stats = {"g1_antes": g1_before, "g1_despues": g1_after, "tol_mm": tol}
    return out, stats
