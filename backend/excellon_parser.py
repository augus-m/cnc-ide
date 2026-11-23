"""Parser de Excellon (.drl) para taladrado de PCB."""

import re
import math


class ExcellonParser:
    def __init__(self):
        self.units_mm = True
        self.int_digits = 3
        self.dec_digits = 3
        self.tools = {}
        self.current_tool = None
        self.holes = []
        self.warnings = []

    def parse(self, text):
        self.tools = {}
        self.holes = []
        self.warnings = []
        self.current_tool = None

        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")

        for raw in lines:
            line = raw.strip()
            if not line:
                continue

            if line == "M48":
                continue
            if line == "%" or line == "M95":
                continue

            if line.startswith("METRIC"):
                self.units_mm = True
                continue
            if line.startswith("INCH"):
                self.units_mm = False
                continue

            m = re.match(r"^T(\d+)C([\d.]+)$", line)
            if m:
                tool_num = int(m.group(1))
                diameter = float(m.group(2))
                if not self.units_mm:
                    diameter *= 25.4
                self.tools[tool_num] = diameter
                continue

            m = re.match(r"^T(\d+)$", line)
            if m:
                tool_num = int(m.group(1))
                if tool_num == 0:
                    self.current_tool = None
                elif tool_num in self.tools:
                    self.current_tool = tool_num
                continue

            if line in ("G90", "G05", "M30", "FMAT,2", "DETECT,ON", "DETECT,OFF"):
                continue
            if line.startswith(";") or line.startswith("G04"):
                continue

            m = re.match(r"^X([-\d.]+)Y([-\d.]+)$", line)
            if m and self.current_tool is not None:
                x = self._parse_coord(m.group(1))
                y = self._parse_coord(m.group(2))
                self.holes.append((x, y, self.tools[self.current_tool]))

        return self.holes

    def _parse_coord(self, s):
        sign = -1 if s.startswith('-') else 1
        s_abs = s.lstrip('+-')
        if '.' in s_abs:
            value = float(s_abs)
        else:
            if len(s_abs) <= self.dec_digits:
                value = int(s_abs) / (10 ** self.dec_digits)
            else:
                int_part = s_abs[:-self.dec_digits]
                dec_part = s_abs[-self.dec_digits:]
                value = int(int_part) + int(dec_part) / (10 ** self.dec_digits)
        if not self.units_mm:
            value *= 25.4
        return sign * value


def excellon_to_gcode(drl_text, feed=100.0, rapid=3000.0,
                      drill_depth=-1.8, safe_z=2.0,
                      origin_x=0.0, origin_y=0.0, ref_bounds=None,
                      rotation=0.0, mirror_x=False, mirror_y=False):
    parser = ExcellonParser()
    holes = parser.parse(drl_text)

    if not holes:
        raise ValueError("No se encontraron taladros")

    # Mismo orden que el cobre: espejo y luego rotacion alrededor del origen.
    holes = transform_holes(holes, rotation, mirror_x, mirror_y)

    by_tool = {}
    for x, y, d in holes:
        by_tool.setdefault(round(d, 2), []).append((x, y))

    minx = min(h[0] for h in holes)
    miny = min(h[1] for h in holes)
    # Marco comun con el cobre (ref_bounds): mismo origen que la capa de referencia.
    if ref_bounds is not None:
        minx, miny = ref_bounds[0], ref_bounds[1]
    ox = origin_x - minx
    oy = origin_y - miny

    lines = []
    lines.append("; CNC IDE -- Excellon a G-code (taladrado)")
    lines.append("; =====================================")
    lines.append(f"; Total taladros: {len(holes)}")
    lines.append(f"; Herramientas: {len(by_tool)}")
    lines.append("G21")
    lines.append("G90")
    lines.append("M301")

    for diameter in sorted(by_tool.keys()):
        positions = by_tool[diameter]
        lines.append("")
        lines.append(f"; --- Broca {diameter:.2f} mm ({len(positions)} agujeros) ---")
        for (x, y) in positions:
            gx = x + ox
            gy = y + oy
            lines.append(f"G0 X{gx:.3f} Y{gy:.3f}")
            lines.append(f"G0 Z{safe_z:.3f}")

    lines.append("")
    lines.append(f"G0 X{origin_x:.3f} Y{origin_y:.3f}")
    lines.append("M302")
    lines.append("M30")

    return lines, {
        "total_holes": len(holes),
        "tools": len(by_tool),
        "by_tool": {d: len(p) for d, p in by_tool.items()},
    }


def transform_holes(holes, rotation=0.0, mirror_x=False, mirror_y=False):
    """Espejo y rotacion (antihorario, alrededor de 0,0), igual que el Gerber."""
    import math
    a = math.radians(float(rotation or 0.0))
    c, s = math.cos(a), math.sin(a)
    out = []
    for x, y, d in holes:
        if mirror_x:
            x = -x
        if mirror_y:
            y = -y
        if rotation:
            x, y = x * c - y * s, x * s + y * c
        out.append((x, y, d))
    return out
