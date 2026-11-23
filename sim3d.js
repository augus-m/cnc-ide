/* ============================================================
   sim3d.js - Simulador 3D WebGL (Three.js r147, version clasica)
   Fase A: placa 3D, herramienta animada (pluma o fresa) y marcas
   sobre la placa segun el G-code (M280 / G1 Z).
   Se alimenta desde index.html: setProgram / setTool / setRunning.
   Los ajustes se guardan en localStorage (clave cnc.sim3d.settings.v1).
   ============================================================ */
(function () {
'use strict';
if (typeof THREE === 'undefined' || !THREE.OrbitControls) { console.warn('[sim3d] Three.js no disponible'); return; }
const vz = document.getElementById('visualizerCanvas');
if (!vz) return;

/* ---------------- ajustes ---------------- */
const LS_KEY = 'cnc.sim3d.settings.v1';
const DEFAULTS = {
  mode: 'pen',            // 'pen' | 'mill'
  penWidth: 1.0,          // mm de trazo real del plumon
  penColor: '#111111',
  millDia: 2.0,           // mm
  millDepth: 0.15,        // mm de desbaste cuando el G-code no trae Z
  material: 'fr4',
  stockAuto: true, stockMargin: 5,
  stockX: 100, stockY: 100, stockZ: 1.6, stockOx: 0, stockOy: 0,
  showPaths: true, showRapids: true, showTool: true
};
const cfg = Object.assign({}, DEFAULTS);
try { const s = localStorage.getItem(LS_KEY); if (s) Object.assign(cfg, JSON.parse(s)); } catch (e) {}
const saveCfg = () => { try { localStorage.setItem(LS_KEY, JSON.stringify(cfg)); } catch (e) {} };

const MATS = {
  fr4:    {name: 'FR4 verde', base: '#1e6b3c', side: '#c9b27c', rough: 0.55, metal: 0.0},
  paper:  {name: 'Papel',     base: '#f1eee4', side: '#d8d3c3', rough: 0.95, metal: 0.0},
  wood:   {name: 'Madera',    base: '#b78a55', side: '#9a7040', rough: 0.80, metal: 0.0, grain: true},
  alu:    {name: 'Aluminio',  base: '#b5bac2', side: '#9aa0a8', rough: 0.40, metal: 0.25, brushed: true},
  copper: {name: 'Cobre',     base: '#b87333', side: '#8f5a28', rough: 0.45, metal: 0.30}
};
const PEN_LIFT = 4;        // mm que sube la pluma al levantarse (solo visual)
const TOL2 = 0.02 * 0.02;  // tolerancia (mm^2) para ubicar la posicion sobre un segmento

/* ---------------- DOM ---------------- */
const wrap = document.createElement('div');
wrap.id = 'glWrap';
wrap.style.cssText = 'position:absolute;inset:0;z-index:1;display:none;overflow:hidden;';
const canvas = document.createElement('canvas');
canvas.style.cssText = 'width:100%;height:100%;display:block;outline:none;touch-action:none;cursor:grab;';
wrap.appendChild(canvas);
const bar = document.createElement('div');
bar.style.cssText = 'position:absolute;left:12px;top:12px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;z-index:2;pointer-events:none;';
bar.innerHTML =
  '<div class="view-tabs" style="pointer-events:auto">' +
  ['iso:Isométrica', 'top:Superior', 'front:Frontal', 'fit:Ajustar'].map(function (p) {
    const a = p.split(':'); return '<div class="view-tab" data-gl="' + a[0] + '">' + a[1] + '</div>'; }).join('') +
  '<div class="view-tab" data-gl="clear">Limpiar marcas</div></div>' +
  '<div id="glInfo" style="pointer-events:none;font-size:11px;font-weight:600;color:var(--text-muted);padding:6px 10px;border-radius:9px;background:var(--widget-bg);border:1px solid var(--glass-border)"></div>';
wrap.appendChild(bar);
const msg = document.createElement('div');
msg.style.cssText = 'position:absolute;inset:0;display:none;align-items:center;justify-content:center;color:var(--text-muted);font-size:13px;z-index:3;text-align:center;padding:20px';
wrap.appendChild(msg);
vz.appendChild(wrap);

/* ---------------- renderer / escena ---------------- */
let renderer;
try {
  renderer = new THREE.WebGLRenderer({canvas: canvas, antialias: true});
} catch (e) {
  console.warn('[sim3d] WebGL no disponible', e);
  msg.style.display = 'flex'; msg.textContent = 'WebGL no está disponible en este equipo.';
  window.sim3d = {show: function (on) { wrap.style.display = on ? 'block' : 'none'; }, setProgram: function () {}, setTool: function () {}, setRunning: function () {}};
  return;
}
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputEncoding = THREE.sRGBEncoding;
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, 1, 0.5, 6000);
camera.up.set(0, 0, 1);                       // Z hacia arriba, igual que el G-code
const controls = new THREE.OrbitControls(camera, canvas);
controls.enableDamping = true; controls.dampingFactor = 0.12; controls.screenSpacePanning = true;
controls.maxPolarAngle = Math.PI * 0.495;
controls.mouseButtons = {LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN};
scene.add(new THREE.AmbientLight(0xffffff, 0.75));
const sun = new THREE.DirectionalLight(0xffffff, 0.85); sun.position.set(-120, -160, 260); scene.add(sun);
const fill = new THREE.DirectionalLight(0xffffff, 0.25); fill.position.set(200, 120, 80); scene.add(fill);

const isDark = function () { const a = document.getElementById('app'); return !['white', 'text'].includes(a && a.getAttribute('data-theme')); };
let gridObj = null, areaObj = null, axesObj = null;
function applyTheme() {
  scene.background = new THREE.Color(isDark() ? 0x101216 : 0xe4e7ec);
  if (gridObj) { gridObj.material.dispose(); gridObj.material = new THREE.LineBasicMaterial({color: isDark() ? 0x3a3f49 : 0xaab0bb, transparent: true, opacity: 0.55}); }
}
function buildEnvironment() {
  const g = new THREE.GridHelper(400, 40, 0x555b66, 0x555b66); g.rotation.x = Math.PI / 2; g.position.set(100, 100, -0.2);
  gridObj = g; scene.add(g);
  const pts = [0, 0, 0.05, 200, 0, 0.05, 200, 0, 0.05, 200, 200, 0.05, 200, 200, 0.05, 0, 200, 0.05, 0, 200, 0.05, 0, 0, 0.05];
  const ag = new THREE.BufferGeometry(); ag.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
  areaObj = new THREE.LineSegments(ag, new THREE.LineBasicMaterial({color: 0xa855f7, transparent: true, opacity: 0.6})); scene.add(areaObj);
  const ax = [0, 0, 0, 30, 0, 0, 0, 0, 0, 0, 30, 0, 0, 0, 0, 0, 0, 30], col = [1, .2, .2, 1, .2, .2, .2, 1, .2, .2, 1, .2, .3, .5, 1, .3, .5, 1];
  const xg = new THREE.BufferGeometry(); xg.setAttribute('position', new THREE.Float32BufferAttribute(ax, 3)); xg.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  axesObj = new THREE.LineSegments(xg, new THREE.LineBasicMaterial({vertexColors: true})); scene.add(axesObj);
  applyTheme();
}
buildEnvironment();
new MutationObserver(applyTheme).observe(document.getElementById('app') || document.body, {attributes: true, attributeFilter: ['data-theme']});

/* ---------------- programa ---------------- */
let segs = [], bounds = null, hasZ = false, progSig = '';
const isRapid = function (s) { return s[4] === 'rapid'; };
function marking(s) {
  if (cfg.mode === 'pen') return !isRapid(s) && (!hasZ || Math.min(s[6], s[7]) <= 0.001);
  return !isRapid(s) && (!hasZ || Math.min(s[6], s[7]) < -0.001);
}
function zOn(s, x, y) {                       // profundidad de corte (<=0) sobre el segmento s en (x,y)
  if (!hasZ) return -Math.abs(+cfg.millDepth || 0.15);
  const dx = s[2] - s[0], dy = s[3] - s[1], l2 = dx * dx + dy * dy;
  const t = l2 > 1e-12 ? Math.max(0, Math.min(1, ((x - s[0]) * dx + (y - s[1]) * dy) / l2)) : 1;
  return Math.min(0, s[6] + (s[7] - s[6]) * t);
}
function analyse() {
  hasZ = false; bounds = null;
  for (let i = 0; i < segs.length; i++) if (Math.abs(segs[i][6]) > 1e-6 || Math.abs(segs[i][7]) > 1e-6) { hasZ = true; break; }
  // la placa se ajusta al dibujo (segmentos que marcan); si no hay ninguno, a todo el recorrido
  let any = false;
  for (let i = 0; i < segs.length; i++) if (marking(segs[i])) { any = true; break; }
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i];
    if (any && !marking(s)) continue;
    const x0 = Math.min(s[0], s[2]), x1 = Math.max(s[0], s[2]), y0 = Math.min(s[1], s[3]), y1 = Math.max(s[1], s[3]);
    if (!bounds) bounds = {x0: x0, y0: y0, x1: x1, y1: y1};
    else { if (x0 < bounds.x0) bounds.x0 = x0; if (y0 < bounds.y0) bounds.y0 = y0; if (x1 > bounds.x1) bounds.x1 = x1; if (y1 > bounds.y1) bounds.y1 = y1; }
  }
}
function signature(list) {
  const n = list.length; if (!n) return '0';
  let h = n + '|'; const step = Math.max(1, Math.floor(n / 64));
  for (let i = 0; i < n; i += step) { const s = list[i]; h += s[0].toFixed(2) + ',' + s[1].toFixed(2) + ',' + s[2].toFixed(2) + ',' + s[3].toFixed(2) + ',' + s[4] + ';'; }
  const l = list[n - 1]; return h + l[2].toFixed(2) + ',' + l[3].toFixed(2);
}

/* ---------------- placa (rectangulo 3D + textura de marcas) ---------------- */
const stock = {group: null, tex: null, cv: null, ctx: null, base: null, ppm: 1, x0: 0, y0: 0, w: 100, h: 100, t: 1.6, dirty: false, lastUp: 0,
  geo: null, hm: null, nx: 1, ny: 1, cx: 1, cy: 1, r0: 1e9, r1: -1, hmDirty: false};
function stockRect() {
  if (!cfg.stockAuto) return {x0: +cfg.stockOx, y0: +cfg.stockOy, w: Math.max(5, +cfg.stockX), h: Math.max(5, +cfg.stockY)};
  if (!bounds) return {x0: 0, y0: 0, w: 100, h: 100};
  const m = Math.max(0, +cfg.stockMargin);
  return {x0: bounds.x0 - m, y0: bounds.y0 - m, w: Math.max(10, bounds.x1 - bounds.x0 + 2 * m), h: Math.max(10, bounds.y1 - bounds.y0 + 2 * m)};
}
function drawBase(ctx, w, h, m) {
  ctx.fillStyle = m.base; ctx.fillRect(0, 0, w, h);
  const n = Math.min(40000, Math.round(w * h / 70));
  for (let i = 0; i < n; i++) {
    const a = (0.02 + Math.random() * 0.05).toFixed(3);
    ctx.fillStyle = Math.random() < 0.5 ? 'rgba(255,255,255,' + a + ')' : 'rgba(0,0,0,' + a + ')';
    ctx.fillRect(Math.random() * w, Math.random() * h, 1 + Math.random() * 2, 1 + Math.random() * 2);
  }
  if (m.grain) {
    for (let i = 0; i < Math.round(h / 6); i++) {
      const y = Math.random() * h; ctx.strokeStyle = 'rgba(70,40,15,' + (0.08 + Math.random() * 0.12).toFixed(2) + ')'; ctx.lineWidth = 1 + Math.random() * 2;
      ctx.beginPath(); ctx.moveTo(0, y);
      for (let x = 0; x <= w; x += w / 8) ctx.lineTo(x, y + Math.sin(x * 0.01 + i) * 3);
      ctx.stroke();
    }
  }
  if (m.brushed) {
    for (let i = 0; i < Math.round(h / 1.5); i++) {
      ctx.strokeStyle = 'rgba(255,255,255,' + (Math.random() * 0.06).toFixed(3) + ')'; ctx.lineWidth = 1;
      const y = Math.random() * h; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y + (Math.random() - 0.5) * 2); ctx.stroke();
    }
  }
}
function disposeObj(o) {
  o.traverse(function (c) {
    if (c.geometry) c.geometry.dispose();
    if (c.material) { (Array.isArray(c.material) ? c.material : [c.material]).forEach(function (mt) { if (mt.map) mt.map.dispose(); mt.dispose(); }); }
  });
}
function buildStock() {
  if (stock.group) { scene.remove(stock.group); disposeObj(stock.group); }
  const r = stockRect(), m = MATS[cfg.material] || MATS.fr4;
  stock.x0 = r.x0; stock.y0 = r.y0; stock.w = r.w; stock.h = r.h; stock.t = Math.max(0.2, +cfg.stockZ || 1.6);
  const longSide = Math.max(r.w, r.h);
  stock.ppm = Math.min(40, 2048 / longSide);
  const cw = Math.max(16, Math.round(r.w * stock.ppm)), ch = Math.max(16, Math.round(r.h * stock.ppm));
  stock.cv = document.createElement('canvas'); stock.cv.width = cw; stock.cv.height = ch; stock.ctx = stock.cv.getContext('2d');
  drawBase(stock.ctx, cw, ch, m);
  stock.base = document.createElement('canvas'); stock.base.width = cw; stock.base.height = ch; stock.base.getContext('2d').drawImage(stock.cv, 0, 0);
  const tex = new THREE.CanvasTexture(stock.cv);
  tex.encoding = THREE.sRGBEncoding; tex.anisotropy = renderer.capabilities.getMaxAnisotropy(); tex.generateMipmaps = true;
  tex.minFilter = THREE.LinearMipmapLinearFilter; stock.tex = tex;
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(r.w, r.h, stock.t), new THREE.MeshStandardMaterial({color: m.side, roughness: 0.85}));
  body.position.set(r.x0 + r.w / 2, r.y0 + r.h / 2, -stock.t / 2);
  const mill = cfg.mode === 'mill';
  const cell = Math.max(0.25, Math.sqrt(r.w * r.h / 160000));      // <= ~160k vertices
  stock.nx = mill ? Math.max(1, Math.round(r.w / cell)) : 1; stock.ny = mill ? Math.max(1, Math.round(r.h / cell)) : 1;
  stock.cx = r.w / stock.nx; stock.cy = r.h / stock.ny;
  stock.hm = mill ? new Float32Array((stock.nx + 1) * (stock.ny + 1)) : null; stock.r0 = 1e9; stock.r1 = -1; stock.hmDirty = false;
  stock.geo = new THREE.PlaneGeometry(r.w, r.h, stock.nx, stock.ny);
  const top = new THREE.Mesh(stock.geo, new THREE.MeshStandardMaterial({map: tex, roughness: m.rough, metalness: m.metal}));
  top.position.set(r.x0 + r.w / 2, r.y0 + r.h / 2, 0.02);
  g.add(body, top); scene.add(g); stock.group = g;
  if (gridObj) gridObj.position.z = -stock.t - 0.2;
  stock.dirty = true;
}
function clearInk() {
  if (!stock.ctx) return;
  stock.ctx.globalCompositeOperation = 'source-over'; stock.ctx.drawImage(stock.base, 0, 0); stock.dirty = true;
  if (stock.hm && stock.geo) {
    stock.hm.fill(0);
    const pa = stock.geo.attributes.position.array, na = stock.geo.attributes.normal.array;
    for (let i = 0; i < stock.hm.length; i++) { pa[i * 3 + 2] = 0; na[i * 3] = 0; na[i * 3 + 1] = 0; na[i * 3 + 2] = 1; }
    stock.geo.attributes.position.needsUpdate = true; stock.geo.attributes.normal.needsUpdate = true; stock.r0 = 1e9; stock.r1 = -1; stock.hmDirty = false;
  }
}
const px = function (x) { return (x - stock.x0) * stock.ppm; };
const py = function (y) { return (stock.h - (y - stock.y0)) * stock.ppm; };
function inkStyle(c) {
  c.lineCap = 'round'; c.lineJoin = 'round';
  if (cfg.mode === 'pen') { c.strokeStyle = c.fillStyle = cfg.penColor; c.lineWidth = Math.max(1, cfg.penWidth * stock.ppm); }
  else { c.strokeStyle = c.fillStyle = '#2b1f14'; c.lineWidth = Math.max(1, cfg.millDia * stock.ppm); }
}
function strokeLine(ax, ay, bx, by, s, zfb) {
  const c = stock.ctx; if (!c) return; inkStyle(c);
  if (cfg.mode === 'mill') carve(ax, ay, s ? zOn(s, ax, ay) : zfb, bx, by, s ? zOn(s, bx, by) : zfb, Math.max(0.1, cfg.millDia / 2));
  if (Math.abs(bx - ax) < 1e-9 && Math.abs(by - ay) < 1e-9) { c.beginPath(); c.arc(px(ax), py(ay), c.lineWidth / 2, 0, 6.2832); c.fill(); }
  else { c.beginPath(); c.moveTo(px(ax), py(ay)); c.lineTo(px(bx), py(by)); c.stroke(); }
  stock.dirty = true;
}

function carve(ax, ay, az, bx, by, bz, r) {   // desbaste: baja la altura bajo la fresa (fondo plano)
  if (!stock.hm) return;
  const nx = stock.nx, ny = stock.ny, cx = stock.cx, cy = stock.cy;
  const i0 = Math.max(0, Math.floor((Math.min(ax, bx) - r - stock.x0) / cx)), i1 = Math.min(nx, Math.ceil((Math.max(ax, bx) + r - stock.x0) / cx));
  const j0 = Math.max(0, Math.floor((stock.y0 + stock.h - Math.max(ay, by) - r) / cy)), j1 = Math.min(ny, Math.ceil((stock.y0 + stock.h - Math.min(ay, by) + r) / cy));
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy, r2 = r * r;
  let changed = false;
  for (let j = j0; j <= j1; j++) {
    const wy = stock.y0 + stock.h - j * cy;
    for (let i = i0; i <= i1; i++) {
      const wx = stock.x0 + i * cx;
      let t = l2 > 1e-12 ? ((wx - ax) * dx + (wy - ay) * dy) / l2 : 0; t = t < 0 ? 0 : t > 1 ? 1 : t;
      const ex = ax + t * dx - wx, ey = ay + t * dy - wy; if (ex * ex + ey * ey > r2) continue;
      const z = Math.max(-stock.t, az + (bz - az) * t), idx = j * (nx + 1) + i;
      if (z < stock.hm[idx]) { stock.hm[idx] = z; changed = true; }
    }
  }
  if (changed) { if (j0 < stock.r0) stock.r0 = j0; if (j1 > stock.r1) stock.r1 = j1; stock.hmDirty = true; }
}
function updateRelief() {                      // sube al GPU solo las filas modificadas (y sus normales)
  const nx = stock.nx, ny = stock.ny, hm = stock.hm, cx = stock.cx, cy = stock.cy;
  const pa = stock.geo.attributes.position.array, na = stock.geo.attributes.normal.array;
  const a = Math.max(0, stock.r0 - 1), b = Math.min(ny, stock.r1 + 1);
  for (let j = a; j <= b; j++) {
    const ju = Math.max(0, j - 1), jd = Math.min(ny, j + 1);
    for (let i = 0; i <= nx; i++) {
      const idx = j * (nx + 1) + i, il = Math.max(0, i - 1), ir = Math.min(nx, i + 1);
      const gx = (hm[j * (nx + 1) + ir] - hm[j * (nx + 1) + il]) / ((ir - il) * cx);
      const gy = (hm[ju * (nx + 1) + i] - hm[jd * (nx + 1) + i]) / ((jd - ju) * cy);
      const inv = 1 / Math.sqrt(gx * gx + gy * gy + 1);
      pa[idx * 3 + 2] = hm[idx]; na[idx * 3] = -gx * inv; na[idx * 3 + 1] = -gy * inv; na[idx * 3 + 2] = inv;
    }
  }
  stock.geo.attributes.position.needsUpdate = true; stock.geo.attributes.normal.needsUpdate = true;
  stock.r0 = 1e9; stock.r1 = -1; stock.hmDirty = false;
}

/* ---------------- trayectorias 3D ---------------- */
let pathObj = null, rapidObj = null;
function buildPaths() {
  [pathObj, rapidObj].forEach(function (o) { if (o) { scene.remove(o); disposeObj(o); } });
  pathObj = rapidObj = null;
  const cutPts = [], rapPts = [], mill = cfg.mode === 'mill' && hasZ;
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i];
    if (isRapid(s)) { rapPts.push(s[0], s[1], mill ? s[6] : PEN_LIFT * 0.75, s[2], s[3], mill ? s[7] : PEN_LIFT * 0.75); }
    else { const z0 = mill ? s[6] : 0.08, z1 = mill ? s[7] : 0.08; cutPts.push(s[0], s[1], z0, s[2], s[3], z1); }
  }
  function mk(arr, color, op) {
    const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
    const o = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({color: color, transparent: true, opacity: op, depthTest: mill ? false : true}));
    o.renderOrder = 5; scene.add(o); return o;
  }
  if (cutPts.length) pathObj = mk(cutPts, 0xff8c42, 0.75);
  if (rapPts.length) rapidObj = mk(rapPts, 0x4cc9f0, 0.4);
  updateVisibility();
}
function updateVisibility() {
  if (pathObj) pathObj.visible = !!cfg.showPaths;
  if (rapidObj) rapidObj.visible = !!cfg.showRapids;
  toolG.visible = !!cfg.showTool;
}

/* ---------------- herramienta ---------------- */
const toolG = new THREE.Group(); scene.add(toolG);
function buildTool() {
  while (toolG.children.length) { const c = toolG.children[0]; toolG.remove(c); disposeObj(c); }
  const add = function (geo, color, rough, metal, z) {
    geo.translate(0, 0, z);
    toolG.add(new THREE.Mesh(geo, new THREE.MeshStandardMaterial({color: color, roughness: rough, metalness: metal})));
  };
  if (cfg.mode === 'pen') {
    const pw = Math.max(0.1, +cfg.penWidth || 1.0);
    const tipR = Math.min(2.0, Math.max(0.3, pw * 1.5));                                 // la punta ahora refleja el ancho real (0.5 / 1 mm, etc.)
    const tip = new THREE.ConeGeometry(tipR, 6, 28); tip.rotateX(-Math.PI / 2);          // vertice hacia -Z
    add(tip, cfg.penColor, 0.45, 0.05, 0);
    const ferrule = new THREE.CylinderGeometry(2.4, Math.max(tipR, 0.6), 2.4, 28); ferrule.rotateX(Math.PI / 2);
    add(ferrule, '#b9bfc8', 0.25, 0.65, 6 + 1.2);                                        // anillo metalico punta/cuerpo
    const body = new THREE.CylinderGeometry(2.8, 2.8, 28, 28); body.rotateX(Math.PI / 2);
    add(body, '#d9dde3', 0.35, 0.1, 6 + 2.4 + 14);
    const ring = new THREE.CylinderGeometry(2.85, 2.85, 1.6, 28); ring.rotateX(Math.PI / 2);
    add(ring, cfg.penColor, 0.4, 0.05, 6 + 2.4 + 28 + 0.8);                              // franja de tinta
    const cap = new THREE.CylinderGeometry(3.0, 3.0, 4, 28); cap.rotateX(Math.PI / 2);
    add(cap, cfg.penColor, 0.5, 0.0, 6 + 2.4 + 28 + 1.6 + 2);
  } else {
    const r = Math.max(0.3, cfg.millDia / 2);
    const shank = new THREE.CylinderGeometry(r, r, 18, 28); shank.rotateX(Math.PI / 2);
    add(shank, '#e8c547', 0.3, 0.5, 9);                                                 // parte cortante (amarillo)
    const hold = new THREE.CylinderGeometry(Math.max(r, 4), Math.max(r, 4), 26, 28); hold.rotateX(Math.PI / 2);
    add(hold, '#3fcf4f', 0.4, 0.3, 18 + 13);                                            // portaherramientas (verde)
  }
}
const tool = {x: 0, y: 0, z: 0, lift: 1, liftTarget: 1, valid: false, down: false};
function placeTool() {
  const lift = !hasZ ? tool.lift * PEN_LIFT : 0;
  const zb = (cfg.mode === 'mill' && !hasZ) ? -Math.abs(+cfg.millDepth || 0.15) * (1 - tool.lift) : tool.z;
  toolG.position.set(tool.x, tool.y, zb + lift);
}

/* ---------------- pintado siguiendo el G-code ---------------- */
let running = false, ci = 0, lx = null, ly = null, fbx = null, fby = null, lastPos = null;
function segDist2(s, x, y) {
  const ax = s[0], ay = s[1], dx = s[2] - ax, dy = s[3] - ay, l2 = dx * dx + dy * dy;
  let t = l2 > 1e-12 ? ((x - ax) * dx + (y - ay) * dy) / l2 : 0; t = t < 0 ? 0 : t > 1 ? 1 : t;
  const ex = ax + t * dx - x, ey = ay + t * dy - y; return ex * ex + ey * ey;
}
function findSeg(x, y, from) {
  const end = Math.min(segs.length, from + 20000);
  for (let j = from; j < end; j++) if (segDist2(segs[j], x, y) <= TOL2) return j;
  return -1;
}
function advance(x, y, flagDown, z) {
  // Pinta en vivo segun la senal real de "pluma abajo / cortando" (tipo !=
  // 'rapid') que llega del simulador Python en cada tick, en vez de exigir
  // que la posicion coincida casi al pixel con un segmento pre-analizado:
  // esa coincidencia fallaba (sobre todo con PCB multicapa, cada capa
  // re-analizada por separado) y el rastro nunca se pintaba.
  const zfb = hasZ ? Math.min(0, z || 0) : -Math.abs(+cfg.millDepth || 0.15);
  if (flagDown && fbx !== null) strokeLine(fbx, fby, x, y, null, zfb);
  fbx = x; fby = y; lx = x; ly = y;
}
function resetRun() { ci = 0; lx = ly = fbx = fby = null; }

/* ---------------- API para index.html ---------------- */
function setProgram(motion) {
  const raw = Array.isArray(motion) ? motion : [];
  const list = raw.filter(function (s) { return Array.isArray(s) && s.length >= 8 && isFinite(s[0]) && isFinite(s[1]) && isFinite(s[2]) && isFinite(s[3]); });
  if (raw.length && list.length !== raw.length) console.warn('[sim3d] segmentos descartados por formato invalido: ' + (raw.length - list.length) + ' de ' + raw.length);
  const sg = signature(list);
  if (sg === progSig) return;
  progSig = sg; segs = list; analyse(); resetRun();
  buildStock(); buildPaths(); clearInk(); fit('iso'); updateInfo();
}
function setTool(x, y, z, valid, cutting, fromSignal) {
  tool.valid = !!valid; if (!valid) return;
  if (running && !fromSignal) return;                  // durante la simulacion solo cuentan las posiciones del simulador
  tool.x = x; tool.y = y; tool.z = z;
  lastPos = {x: x, y: y, z: z, cutting: !!cutting, t: performance.now()};
  const down = cfg.mode === 'pen' ? (!!cutting && (!hasZ || z <= 0.001)) : (hasZ ? z < -0.001 : !!cutting);
  tool.down = down;
  tool.liftTarget = (!hasZ && !down) ? 1 : 0;
  if (running) advance(x, y, down, z);
  if (!running) { lx = null; fbx = null; }
}
function setRunning(r) {
  r = !!r; if (r === running) return;
  running = r;
  if (r) {
    clearInk(); resetRun();
    if (lastPos && performance.now() - lastPos.t < 500) advance(lastPos.x, lastPos.y, false, lastPos.z);  // recupera lo recorrido si el estado llego con retraso
  }
}

/* ---------------- camara / render ---------------- */
function fit(kind) {
  const cx = stock.x0 + stock.w / 2, cy = stock.y0 + stock.h / 2, size = Math.max(stock.w, stock.h, 40);
  const dir = kind === 'top' ? [0, -0.02, 1] : kind === 'front' ? [0, -1, 0.35] : [-0.75, -1.0, 0.85];
  const n = Math.hypot(dir[0], dir[1], dir[2]), dist = size * (kind === 'top' ? 1.35 : 1.15);
  controls.target.set(cx, cy, -stock.t / 2);
  camera.position.set(cx + dir[0] / n * dist, cy + dir[1] / n * dist, -stock.t / 2 + dir[2] / n * dist);
  controls.update();
}
function resize() {
  const w = wrap.clientWidth, h = wrap.clientHeight; if (!w || !h) return;
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
}
let raf = 0;
function frame() {
  raf = requestAnimationFrame(frame);
  controls.update();
  tool.lift += (tool.liftTarget - tool.lift) * 0.25;
  placeTool();
  const now = performance.now();
  if ((stock.dirty || stock.hmDirty) && now - stock.lastUp > 50) {
    if (stock.dirty) { stock.tex.needsUpdate = true; stock.dirty = false; }
    if (stock.hmDirty) updateRelief();
    stock.lastUp = now;
  }
  renderer.render(scene, camera);
}
function show(on) {
  wrap.style.display = on ? 'block' : 'none';
  if (on) { resize(); if (!raf) frame(); }
  else if (raf) { cancelAnimationFrame(raf); raf = 0; }
}
new ResizeObserver(function () { if (wrap.style.display !== 'none') resize(); }).observe(wrap);
bar.querySelectorAll('[data-gl]').forEach(function (b) {
  b.addEventListener('click', function () { const k = b.getAttribute('data-gl'); if (k === 'clear') clearInk(); else fit(k === 'fit' ? 'iso' : k); });
});
function updateInfo() {
  const el = document.getElementById('glInfo'); if (!el) return;
  const m = MATS[cfg.material] || MATS.fr4;
  const t = cfg.mode === 'pen' ? 'Pluma · trazo ' + (+cfg.penWidth).toFixed(2) + ' mm' : 'Fresa · Ø ' + (+cfg.millDia).toFixed(2) + ' mm';
  el.textContent = t + ' · ' + m.name + ' · placa ' + stock.w.toFixed(1) + ' × ' + stock.h.toFixed(1) + ' × ' + stock.t.toFixed(1) + ' mm';
}

/* ---------------- pestaña de Ajustes ---------------- */
function injectSettings() {
  const tabs = document.querySelector('.settings-tabs'), content = document.querySelector('.settings-content'), after = document.getElementById('tab-visor');
  if (!tabs || !content || !after) return;
  const tabBtn = document.createElement('div'); tabBtn.className = 'settings-tab'; tabBtn.textContent = 'Simulador 3D';
  tabBtn.onclick = function () { window.setSettingsTab('tab-sim3d', tabBtn); };
  const ref = Array.prototype.find.call(tabs.children, function (e) { return (e.getAttribute('onclick') || '').indexOf('tab-visor') >= 0; });
  if (ref) ref.after(tabBtn); else tabs.appendChild(tabBtn);
  const num = function (k, step, min) { return '<input type="number" class="settings-input" data-k="' + k + '" step="' + step + '" min="' + min + '" style="width:90px;text-align:right">'; };
  const chk = function (k, label) { return '<div class="settings-checkbox-row"><input type="checkbox" id="s3d_' + k + '" data-k="' + k + '"><label for="s3d_' + k + '">' + label + '</label></div>'; };
  const row = function (label, ctl) { return '<div class="settings-row"><label>' + label + '</label>' + ctl + '</div>'; };
  const sel = function (k, opts) { return '<select class="settings-input" data-k="' + k + '" style="width:160px">' + opts.map(function (o) { return '<option value="' + o[0] + '">' + o[1] + '</option>'; }).join('') + '</select>'; };
  const pane = document.createElement('div'); pane.id = 'tab-sim3d'; pane.className = 'tab-content';
  pane.innerHTML =
    row('Herramienta:', sel('mode', [['pen', 'Pluma / plumón'], ['mill', 'Fresa']])) +
    row('Ancho de trazo de la pluma (mm):', num('penWidth', 0.1, 0.1)) +
    row('Color de tinta:', '<input type="color" data-k="penColor" style="width:54px;height:26px;padding:0;border:1px solid var(--glass-border);border-radius:6px;background:none;cursor:pointer">') +
    row('Diámetro de la fresa (mm):', num('millDia', 0.1, 0.1)) +
    row('Profundidad de desbaste si el G-code no trae Z (mm):', num('millDepth', 0.05, 0.02)) +
    row('Material de la placa:', sel('material', Object.keys(MATS).map(function (k) { return [k, MATS[k].name]; }))) +
    chk('stockAuto', 'Tamaño de placa automático (según el G-code)') +
    row('Margen alrededor del G-code (mm):', num('stockMargin', 1, 0)) +
    row('Placa manual · ancho X (mm):', num('stockX', 1, 5)) +
    row('Placa manual · alto Y (mm):', num('stockY', 1, 5)) +
    row('Grosor de la placa Z (mm):', num('stockZ', 0.1, 0.2)) +
    row('Placa manual · origen X (mm):', num('stockOx', 1, -1000)) +
    row('Placa manual · origen Y (mm):', num('stockOy', 1, -1000)) +
    chk('showPaths', 'Mostrar trayectorias de corte/dibujo') +
    chk('showRapids', 'Mostrar movimientos rápidos / pluma arriba') +
    chk('showTool', 'Mostrar la herramienta');
  after.after(pane);
  pane.querySelectorAll('[data-k]').forEach(function (el) {
    const k = el.getAttribute('data-k');
    if (el.type === 'checkbox') el.checked = !!cfg[k]; else el.value = cfg[k];
    el.addEventListener('change', function () {
      if (el.type === 'checkbox') cfg[k] = el.checked;
      else if (el.type === 'number') { const v = parseFloat(el.value); if (!isFinite(v)) { el.value = cfg[k]; return; } cfg[k] = v; }
      else cfg[k] = el.value;
      saveCfg(); applyConfig(k);
    });
  });
}
function applyConfig(k) {
  if (k === 'mode') { analyse(); buildStock(); buildTool(); buildPaths(); clearInk(); fit('iso'); }
  else if (k === 'penWidth' || k === 'penColor' || k === 'millDia') buildTool();
  else if (k === 'showPaths' || k === 'showRapids' || k === 'showTool') updateVisibility();
  else { buildStock(); clearInk(); fit('iso'); }       // material / placa
  updateInfo();
}

/* ---------------- arranque ---------------- */
buildStock(); buildTool(); buildPaths(); clearInk(); fit('iso'); updateInfo(); injectSettings();
window.sim3d = {
  show: show, setProgram: setProgram, setTool: setTool, setRunning: setRunning,
  clearInk: clearInk, fit: fit, cfg: cfg,
  _dbg: {
    renderer: renderer, scene: scene, camera: camera, stock: stock, tool: tool,
    info: function () { return {segs: segs.length, hasZ: hasZ, bounds: bounds, stock: {x0: stock.x0, y0: stock.y0, w: stock.w, h: stock.h, t: stock.t, ppm: stock.ppm}, running: running, ci: ci}; },
    relief: function () { if (!stock.hm) return null; let n = 0, mn = 0; for (let i = 0; i < stock.hm.length; i++) if (stock.hm[i] < -1e-6) { n++; if (stock.hm[i] < mn) mn = stock.hm[i]; } return {vertices: stock.hm.length, cut: n, minDepth: mn}; },
    shot: function () { renderer.render(scene, camera); return canvas.toDataURL('image/png'); },
    inkPixels: function () { const c = stock.ctx, d = c.getImageData(0, 0, stock.cv.width, stock.cv.height).data, b = stock.base.getContext('2d').getImageData(0, 0, stock.cv.width, stock.cv.height).data; let n = 0; for (let i = 0; i < d.length; i += 4) if (Math.abs(d[i] - b[i]) + Math.abs(d[i + 1] - b[i + 1]) + Math.abs(d[i + 2] - b[i + 2]) > 60) n++; return n; }
  }
};
})();
