"""Prueba de humo del Simulador 3D (WebGL) con el G-code real.

Carga index.html en un QWebEngineView aparte, inyecta los segmentos de
/tmp/segs_plumon.json, recorre el simulador a pasos de 8 mm (como el
SimulationController) y guarda capturas en /tmp/sim3d_*.png.
Uso:  QT_QPA_PLATFORM=offscreen python3 tests/render_sim3d.py [pen|mill]
"""
import base64
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                      "--use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist --no-sandbox")
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

APP_DIR = Path(__file__).resolve().parents[1]
MODE = sys.argv[1] if len(sys.argv) > 1 else "pen"
SEGS = sys.argv[2] if len(sys.argv) > 2 else "/tmp/segs_plumon.json"
segs = json.load(open(SEGS))


class Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        print(f"[js:{level.name}] {message} ({Path(source).name}:{line})")


app = QApplication(sys.argv)
view = QWebEngineView()
profile = QWebEngineProfile(view)   # sin nombre = off-the-record
page = Page(profile, view)
view.setPage(page)
view.resize(1400, 850)
view.show()

WALK = """
(function(){
  const segs = %s;
  const api = window.viewerAPI, s3 = window.sim3d;
  const sel = document.querySelector('#tab-sim3d [data-k=mode]'); sel.value = '%s'; sel.dispatchEvent(new Event('change'));
  api.setHostProgram({motion: segs, layers: {}, holes: {}});
  api.setView('gl');
  s3.setRunning(true);
  const STEP = 8.0;                       // mm por tick, igual que 500 mm/s a 60 Hz
  let budget = STEP, n = 0;
  api.setHostPosition(segs[0][0], segs[0][1], segs[0][6], true, segs[0][4] !== 'rapid');
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i];
    const len = Math.hypot(s[2]-s[0], s[3]-s[1], s[7]-s[6]);
    let used = 0;
    while (len - used > budget) {
      used += budget; budget = STEP; const t = len > 0 ? used/len : 1;
      api.setHostPosition(s[0]+(s[2]-s[0])*t, s[1]+(s[3]-s[1])*t, s[6]+(s[7]-s[6])*t, true, s[4] !== 'rapid'); n++;
    }
    budget -= (len - used);
  }
  const l = segs[segs.length-1];
  api.setHostPosition(l[2], l[3], l[7], true, l[4] !== 'rapid');
  return JSON.stringify({ticks: n, info: s3._dbg.info(), ink: s3._dbg.inkPixels()});
})()
"""


def shot(name, then):
    page.runJavaScript("window.sim3d._dbg.shot()", lambda data: save(name, data, then))


def save(name, data, then):
    if data and data.startswith("data:image/png;base64,"):
        Path(f"/tmp/{name}.png").write_bytes(base64.b64decode(data.split(",", 1)[1]))
        print("captura:", f"/tmp/{name}.png", len(data) // 1024, "KB")
    else:
        print("captura vacia", name)
    then()


def after_load(ok):
    print("loadFinished:", ok)
    if not ok:
        app.quit(); return
    QTimer.singleShot(1500, run)


def run():
    page.runJavaScript("typeof THREE + '|' + typeof window.sim3d + '|' + (window.sim3d && Object.keys(window.sim3d).join(','))",
                       lambda r: print("estado JS:", r))
    page.runJavaScript(WALK % (json.dumps(segs), MODE), got_walk)


def got_walk(res):
    print("recorrido:", res)
    QTimer.singleShot(800, lambda: shot(f"sim3d_{MODE}_iso", lambda: page.runJavaScript(
        "window.sim3d.fit('top'); 1", lambda _: QTimer.singleShot(600, lambda: shot(f"sim3d_{MODE}_top", open_settings)))))


def open_settings():
    page.runJavaScript("""
      (function(){ window.sim3d.fit('iso');
        const m = document.getElementById('settingsModal'); m.classList.add('active');
        const tab = [...document.querySelectorAll('.settings-tab')].find(e => e.textContent.trim() === 'Simulador 3D');
        if (tab) tab.click(); return tab ? 'pestana ok' : 'SIN pestana'; })()""",
        lambda r: (print("ajustes:", r), QTimer.singleShot(700, grab_full)))


def grab_full():
    view.grab().save(f"/tmp/sim3d_{MODE}_ventana.png")
    print("captura ventana:", f"/tmp/sim3d_{MODE}_ventana.png")
    app.quit()


page.loadFinished.connect(after_load)
view.setUrl(QUrl.fromLocalFile(str(APP_DIR / "index.html")))
QTimer.singleShot(120000, app.quit)
sys.exit(app.exec())
