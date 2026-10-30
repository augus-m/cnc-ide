(() => {
  const byId = id => document.getElementById(id);
  let api = null;
  let lastState = null;
  let lastConsole = '';
  let consoleInitialized = false;
  let editorDirty = false;
  let lastAppliedTheme = null;
  let lastMcpClientSignature = '';
  let lastFileListSignature = null;

  function toast(message, type = '') {
    let node = byId('cncToast');
    if (!node) {
      node = document.createElement('div');
      node.id = 'cncToast';
      node.style.cssText = 'position:fixed;left:50%;bottom:82px;transform:translateX(-50%) translateY(12px);z-index:10050;opacity:0;pointer-events:none;padding:10px 16px;border-radius:16px;border:1px solid var(--glass-border);background:var(--glass-bg);backdrop-filter:blur(24px) saturate(180%);box-shadow:0 14px 40px rgba(0,0,0,.35);font-weight:600;transition:all .24s ease';
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.style.color = type === 'error' ? 'var(--apple-red)' : 'var(--text-main)';
    node.style.opacity = '1'; node.style.transform = 'translateX(-50%) translateY(0)';
    clearTimeout(node._timer);
    node._timer = setTimeout(() => { node.style.opacity = '0'; node.style.transform = 'translateX(-50%) translateY(12px)'; }, 2500);
  }

  function showConnectDialog() {
    let overlay = byId('cncConnectOverlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'cncConnectOverlay';
      overlay.className = 'settings-overlay';
      overlay.innerHTML = `
        <div class="settings-window" style="width:min(440px,calc(100vw - 32px));height:auto;min-height:0;">
          <div class="settings-titlebar"><div class="traffic-lights"><button class="tl tl-close" aria-label="Cerrar"></button></div><h2>Conectar CNC</h2></div>
          <div class="settings-content">
            <div class="settings-main-title">Conexión serie</div>
            <div class="settings-row"><label for="cncPortSelect">Puerto</label><select class="settings-input" id="cncPortSelect" style="width:220px"></select></div>
            <div class="settings-row"><label for="cncBaudSelect">Baudrate</label><select class="settings-input" id="cncBaudSelect" style="width:220px">${[9600,19200,38400,57600,115200,230400,460800,921600].map(x=>`<option value="${x}" ${x===115200?'selected':''}>${x}</option>`).join('')}</select></div>
            <div class="helper-text" id="cncPortHint">Selecciona el puerto USB serie de la controladora.</div>
          </div>
          <div class="settings-footer"><button class="settings-btn-action btn-cancel" data-action="cancel">Cancelar</button><button class="settings-btn-action btn-save" data-action="connect">Conectar</button></div>
        </div>`;
      document.body.appendChild(overlay);
      overlay.addEventListener('click', e => { if (e.target === overlay || e.target.closest('[data-action="cancel"]') || e.target.closest('.tl-close')) overlay.classList.remove('active'); });
      overlay.querySelector('[data-action="connect"]').addEventListener('click', () => {
        const port = byId('cncPortSelect').value;
        const baud = Number(byId('cncBaudSelect').value);
        if (!port) { byId('cncPortHint').textContent = 'No hay puertos serie disponibles.'; return; }
        api.connectToPort(port, baud);
        overlay.classList.remove('active');
      });
    }
    const select = byId('cncPortSelect');
    const hint = byId('cncPortHint');
    select.innerHTML = '<option value="">Buscando puertos…</option>';
    api.availablePorts(ports => {
      select.innerHTML = '';
      (ports || []).forEach(port => {
        const option = document.createElement('option');
        option.value = port.device;
        option.textContent = `${port.device} — ${port.description || ''}`;
        select.appendChild(option);
      });
      if (!select.options.length) {
        select.innerHTML = '<option value="">(ningún puerto detectado)</option>';
        hint.textContent = 'Conecta la placa y vuelve a abrir este diálogo.';
      } else hint.textContent = 'Selecciona el puerto USB serie de la controladora.';
    });
    overlay.classList.add('active');
  }

  function renderState(state) {
    if (!state) return;
    lastState = state;
    const settingsModal = byId('settingsModal');
    if (!settingsModal?.classList.contains('active')) {
      const axes = state.axesEnabled || {X:true, Y:true, Z:false};
      ['X','Y','Z'].forEach(axis => { const input=byId(`axisEnable${axis}`); if(input) input.checked=!!axes[axis]; });
      window.setEnabledAxesUI?.(axes);
    }
    window.setMotorPulseTelemetry?.(state.pulseStats || {}, !!state.pulseTelemetry, !!state.connected, false);
    if (!state.simulationRunning) {
      const pauseButton = byId('simPauseButton');
      if (pauseButton && pauseButton.textContent.trim() !== 'Pausar') {
        pauseButton.innerHTML = '<svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg> Pausar';
      }
    }
    if (typeof window.setHostConnection === 'function') window.setHostConnection(!!state.connected, !!state.polling);
    const status = byId('connStatus'), connect = byId('btnConnect'), polling = byId('btnPolling');
    if (status) {
      status.textContent = state.connected ? `Conectado${state.port ? ' · ' + state.port : ''}` : 'Desconectado';
      status.classList.toggle('connected', !!state.connected);
    }
    if (connect) connect.textContent = state.connected ? 'Desconectar' : 'Conectar';
    if (polling) polling.textContent = `Polling: ${state.polling ? 'ON' : 'OFF'}`;

    const position = state.position || {};
    if (window.viewerAPI) window.viewerAPI.setHostPosition(position.x, position.y, position.z, !!state.positionValid);
    if (state.theme && state.theme !== lastAppliedTheme && typeof window.setTheme === 'function') {
      lastAppliedTheme = state.theme;
      window.setTheme(state.theme);
    }
    const widgets = document.querySelectorAll('.sidebar-right .widget');
    const coords = widgets[0];
    if (coords) {
      const valid=!!state.positionValid;
      const values = {X:position.x, Y:position.y, Z:position.z};
      Object.entries(values).forEach(([axis,value]) => {
        const target = coords.querySelector(`[data-axis="${axis}"] .val-data`);
        if (target) target.textContent = valid ? Number(value || 0).toFixed(2) : '?';
      });
    }
    const statWidget = widgets[1]?.querySelectorAll('.val-data');
    if (statWidget?.length) {
      const s = state.stats || {};
      [s.total,s.g0,s.g1,s.arcs,s.mcodes,s.distance,s.bounds,s.boundary].forEach((value,i)=>{ if(statWidget[i]) statWidget[i].textContent = value ?? '-'; });
    }
    const stateRow = widgets[2]?.querySelector('.status-badge');
    if (stateRow) stateRow.textContent = state.simulationRunning ? 'Simulación' : (state.machineState || (state.jobRunning ? 'En marcha' : 'Listo'));
    const lineCount=widgets[2]?.querySelector('.data-row .val-data');
    if(lineCount) lineCount.textContent=state.lineCount || '-';
    const progress = widgets[2]?.querySelector('.progress-bar-fill');
    if (progress) progress.style.width = `${Math.max(0,Math.min(100,Number(state.progress)||0))}%`;
    const timeRows = widgets[3]?.querySelectorAll('.val-data');
    if (timeRows?.length >= 2) { timeRows[0].textContent = state.elapsed || '00:00:00'; timeRows[1].textContent = state.remaining || '-'; }

    const fo = byId('foSlider'), foLabel = byId('foLabel');
    if (fo && document.activeElement !== fo) fo.value = state.feedOverride ?? 100;
    if (foLabel) foLabel.textContent = `${state.feedOverride ?? 100}%`;
    const g0 = [...document.querySelectorAll('.sidebar-toggle')].find(x=>x.textContent.includes('G0'))?.querySelector('input');
    const g1 = [...document.querySelectorAll('.sidebar-toggle')].find(x=>x.textContent.includes('G1'))?.querySelector('input');
    if (g0) g0.checked = !!state.showRapid;
    if (g1) g1.checked = state.showCuts !== false;
    window.viewerAPI?.setMotionVisibility(!!state.showRapid, state.showCuts !== false);

    const fileList = byId('cncFileList');
    if (fileList) {
      const files = state.files || [];
      const signature = JSON.stringify(files);
      if (signature !== lastFileListSignature) {
        lastFileListSignature = signature;
        fileList.replaceChildren();
        files.forEach((file,index) => {
          const row=document.createElement('div'); row.className='menu-item'; row.textContent=file.name || String(file);
          if(file.path) { row.style.cursor='pointer'; row.title='Doble clic para volver a abrir'; row.addEventListener('dblclick',()=>api.openRecent(index)); }
          fileList.appendChild(row);
        });
      }
    }
    const prefs=document.getElementById('settingsModal');
    if(prefs && !prefs.classList.contains('active')) {
      const poll=document.querySelector('#tab-conexion .settings-input');
      const machine=document.querySelectorAll('#tab-maquina .settings-input');
      if(poll && state.pollInterval) poll.value=`${state.pollInterval} ms`;
      if(machine[0] && state.areaX) machine[0].value=state.areaX;
      if(machine[1] && state.areaY) machine[1].value=state.areaY;
    }
    const jobButtons = document.querySelectorAll('.action-pills .action-pill');
    jobButtons.forEach(button => button.classList.toggle('is-running', !!state.jobRunning));

    const mcp = state.mcp || {};
    const writeToggle = byId('mcpWriteEnabled');
    if (writeToggle && (!prefs || !prefs.classList.contains('active'))) writeToggle.checked = !!mcp.write_enabled;
    const clientList = byId('mcpClientList');
    const clientStatus = byId('mcpClientStatus');
    if (clientList) {
      const clients = mcp.clients || [];
      const signature=JSON.stringify(clients);
      if(signature!==lastMcpClientSignature) {
        lastMcpClientSignature=signature;
        clientList.replaceChildren();
        if (!clients.length) {
          const empty = document.createElement('div'); empty.className='mcp-client-empty';
          empty.textContent='No hay clientes MCP conectados'; clientList.appendChild(empty);
        } else clients.forEach(client => {
          const row=document.createElement('div'); row.className='mcp-client-row';
          const name=document.createElement('strong'); name.textContent=client.name || 'Cliente MCP';
          const detail=document.createElement('span'); detail.textContent=`Activo · PID ${client.pid || '?'}`;
          row.append(name,detail); clientList.appendChild(row);
        });
      }
    }
    if (clientStatus) clientStatus.textContent = (mcp.clients || []).length ? 'Conectado' : 'Sin clientes conectados';
  }

  function renderProgram(program) {
    if (!program) return;
    if (window.viewerAPI) window.viewerAPI.setHostProgram(program);
    document.querySelectorAll('.layer-checkbox').forEach((checkbox,index)=>{
      const names=['F.Cu','B.Cu','Edge.Cuts','F.SilkS','F.Mask','PTH','NPTH'];
      const name=names[index], layer=program.layers?.[name] || program.holes?.[name];
      if (layer) checkbox.checked=!!layer.visible;
    });
    const editor = document.querySelector('.editor-window .gcode-editor');
    if (editor && !editorDirty && document.activeElement !== editor && typeof program.text === 'string') {
      editor.value = program.text;
      editor.dispatchEvent(new Event('input', {bubbles:true}));
      editorDirty = false;
    }
  }

  function renderConsole(text) {
    if (typeof text !== 'string' || (consoleInitialized && text === lastConsole)) return;
    const output = byId('terminalOutput');
    if (!output) return;
    const appendOnly = consoleInitialized && text.startsWith(lastConsole);
    const atBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 24;
    const delta = appendOnly ? text.slice(lastConsole.length) : text;
    if (!appendOnly) output.replaceChildren();
    lastConsole = text;
    consoleInitialized = true;
    delta.split('\n').filter(Boolean).forEach(line => {
      const row = document.createElement('div');
      row.className = 'term-line' + (/ERROR|ALARM|FUERA|No conectado/i.test(line) ? '' : /OK|Cargado|iniciado|conectado|completado/i.test(line) ? ' success' : '');
      row.textContent = line;
      output.appendChild(row);
    });
    while (output.children.length > 1200) output.firstElementChild?.remove();
    if (atBottom) output.scrollTop = output.scrollHeight;
  }

  function activeEditorText() { return document.querySelector('.editor-window .gcode-editor')?.value || ''; }

  function bindEditorWindow(win) {
    const editor = win.querySelector('.gcode-editor');
    if (!editor || editor.dataset.cncBound) return;
    editor.dataset.cncBound = '1';
    editor.addEventListener('input', () => { editorDirty = true; });
    const preview = win.querySelector('[data-editor-action="preview"]');
    if (preview) preview.addEventListener('click', () => { editorDirty = false; api.previewEditor(editor.value); toast('Vista previa actualizada'); });
    const save = win.querySelector('[data-editor-action="save"]');
    if (save) save.addEventListener('click', () => api.saveEditor(editor.value));
  }

  function bindTemplateActions() {
    window.cncBridge = api;
    window.cncBridgeReady = true;
    window.toggleConnection = () => lastState?.connected ? api.disconnectCnc() : showConnectDialog();
    const originalSetTheme = window.setTheme;
    window.setTheme = theme => { originalSetTheme(theme); api.setTheme(theme); };
    window.togglePolling = () => api.setPolling(!lastState?.polling);
    window.actionBtn = action => {
      if (action === 'Iniciar') api.startJob(activeEditorText());
      else if (action === 'Pausar') api.pauseJob();
      else if (action === 'Detener') api.stopJob();
      else if (action === 'E-Stop') api.emergencyStop();
    };
    let simulationPaused = false;
    window.simAction = action => {
      if (action === 'Simular') {
        simulationPaused = false;
        const pauseButton = byId('simPauseButton');
        if (pauseButton) pauseButton.innerHTML = '<svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg> Pausar';
        api.startSimulation();
      } else if (action === 'Pausar') {
        simulationPaused = !simulationPaused;
        api.pauseSimulation();
        const pauseButton = byId('simPauseButton');
        if (pauseButton) pauseButton.innerHTML = simulationPaused
          ? '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg> Reanudar'
          : '<svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg> Pausar';
      } else if (action === 'Detener simulación') {
        simulationPaused = false;
        const pauseButton = byId('simPauseButton');
        if (pauseButton) pauseButton.innerHTML = '<svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg> Pausar';
        api.stopSimulation();
      }
    };
    window.machineCmd = (name, command, x=0, y=0) => api.machineCommand(name, command, Number(x)||0, Number(y)||0);
    window.orderedFabrication = () => api.orderedFabrication();
    const originalShowOnlyLayers = window.showOnlyLayers;
    window.showOnlyLayers = layer => {
      originalShowOnlyLayers(layer);
      api.showOnlyLayer(layer ?? 'null');
    };
    window.resetViewer = () => { api.resetView(); window.viewerAPI?.reset2(); window.viewerAPI?.reset3(); };
    window.saveSettings = () => {
      const poll = document.querySelector('#tab-conexion .settings-input')?.value || '500 ms';
      const machine = document.querySelectorAll('#tab-maquina .settings-input');
      const x = Number(machine[0]?.value || 200), y = Number(machine[1]?.value || 200);
      const alphaText = document.querySelector('#tab-visor .settings-input')?.value || '12';
      const alpha = parseInt(alphaText,10) || 12;
      api.savePreferences(poll,x,y,alpha);
      const axes = {X:!!byId('axisEnableX')?.checked, Y:!!byId('axisEnableY')?.checked, Z:!!byId('axisEnableZ')?.checked};
      api.setEnabledAxes(axes.X,axes.Y,axes.Z);
      window.setEnabledAxesUI?.(axes);
      api.saveMcpConfig(!!byId('mcpWriteEnabled')?.checked);
      window.closeSettings();
      toast('Ajustes guardados');
    };
    window.clearTerminalOutput = () => {
      lastConsole = ''; consoleInitialized = true;
      byId('terminalOutput')?.replaceChildren();
      api.clearConsole();
    };
    window.copyTerminalOutput = () => {
      const value = byId('terminalOutput')?.innerText || '';
      api.copyText(value);
      toast('Terminal copiada');
    };
    const fileActions = new Map([
      ['Abrir G-code','gcode'], ['Convertir SVG','svg'], ['Convertir DXF','dxf'],
      ['Convertir Gerber (PCB)','gerber'], ['Convertir Excellon (drill)','excellon'], ['Guardar G-code','save'],
    ]);
    document.querySelectorAll('#accordionFiles .menu-item').forEach(item => {
      const kind = fileActions.get(item.textContent.trim());
      if (!kind) return;
      item.setAttribute('role','button'); item.tabIndex=0; item.style.cursor='pointer';
      const run = event => {
        event.stopPropagation();
        if(kind==='save' && document.querySelector('.editor-window .gcode-editor')) api.saveEditor(activeEditorText());
        else api.openFile(kind);
      };
      item.addEventListener('click',run);
      item.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();run(e);}});
    });

    const layerQuickActions=byId('accordionLayers')?.querySelectorAll('.mini-btn');
    if(layerQuickActions?.length>=2) {
      const actions=byId('accordionLayers').querySelectorAll('.mini-btn-group .mini-btn');
      if(actions.length>=2) {
        actions[0].onclick=()=>api.orderedFabrication();
        actions[1].onclick=()=>{
          if(confirm('¿Eliminar todas las capas, archivos y el G-code cargado?')) api.clearAll();
        };
      }
    }
    const clearItem=[...document.querySelectorAll('.sidebar .action-btn')].find(item=>item.textContent.includes('Limpiar todo'));
    if(clearItem) clearItem.onclick=()=>{if(confirm('¿Limpiar capas, archivos y G-code?')) api.clearAll();};

    const layerNames=['F.Cu','B.Cu','Edge.Cuts','F.SilkS','F.Mask','PTH','NPTH'];
    document.querySelectorAll('.layer-checkbox').forEach((checkbox,index)=>checkbox.addEventListener('change',()=>api.setLayerVisibility(layerNames[index],checkbox.checked)));
    document.querySelectorAll('.sidebar-toggle input').forEach(input=>input.addEventListener('change',()=>{
      const rapid=[...document.querySelectorAll('.sidebar-toggle')].find(x=>x.textContent.includes('G0'))?.querySelector('input')?.checked;
      const cuts=[...document.querySelectorAll('.sidebar-toggle')].find(x=>x.textContent.includes('G1'))?.querySelector('input')?.checked;
      api.setMotionVisibility(!!rapid, cuts !== false);
    }));
    byId('foSlider')?.addEventListener('change',e=>api.setFeedOverride(Number(e.target.value)));
    byId('foSlider')?.addEventListener('input',e=>{const label=byId('foLabel');if(label)label.textContent=`${e.target.value}%`;});
    document.querySelector('.simulation-bubbles input[type="range"]')?.addEventListener('input',e=>api.setSimulationSpeed(Number(e.target.value)*10));
    byId('stepGrid')?.addEventListener('click',e=>{const step=Number(e.target.closest('.step-chip')?.textContent);if(step)api.setJogStep(step);});
    byId('accordionFab')?.addEventListener('click',e=>{
      const segment=e.target.closest('.segment'); if(!segment) return;
      const group=segment.parentElement;
      if(group===byId('accordionFab').querySelectorAll('.segmented-control')[0]) {
        const modes={Normal:'NORMAL','Rápido':'FAST',Preciso:'PRECISION'}; api.setMotionMode(modes[segment.textContent.trim()]||'NORMAL');
      } else {
        const speeds={Lento:200,Normal:300,'Rápido':2000,Turbo:5000}; const speed=speeds[segment.textContent.trim()]; if(speed)api.setJogSpeed(speed);
      }
    });
    const originalOpen = window.openEditorWindow;
    window.openEditorWindow = () => {
      const win = originalOpen();
      if (!win) return win;
      bindEditorWindow(win);
      api.currentProgram(program => {
        const editor=win.querySelector('.gcode-editor');
        if(editor && !editorDirty && !editor.value) { editor.value=program.text||''; editor.dispatchEvent(new Event('input',{bubbles:true})); editorDirty=false; }
      });
      return win;
    };

    for (const selector of ['#sidebarLeft', '.sidebar-right', '.settings-content']) {
      const container = document.querySelector(selector);
      if (!container || container.dataset.wheelBound) continue;
      container.dataset.wheelBound = '1';
      container.style.overflowY = 'auto';
      container.addEventListener('wheel', event => {
        const previous = container.scrollTop;
        const delta = event.deltaY || (event.deltaX ? event.deltaX : 0);
        container.scrollTop = Math.max(0, Math.min(
          container.scrollHeight - container.clientHeight,
          previous + delta
        ));
        if (container.scrollTop !== previous) event.preventDefault();
      }, {passive:false, capture:true});
    }
  }

  new QWebChannel(qt.webChannelTransport, channel => {
    api = channel.objects.cnc;
    bindTemplateActions();
    api.stateChanged.connect(renderState);
    api.pulseStatsChanged.connect(payload => {
      window.setMotorPulseTelemetry?.(payload.stats || {}, !!payload.telemetryAvailable, !!lastState?.connected, true);
    });
    api.programChanged.connect(renderProgram);
    api.consoleChanged.connect(renderConsole);
    api.positionChanged.connect((x,y,z,valid)=>window.viewerAPI?.setHostPosition(x,y,z,valid));
    api.initialState(renderState);
    api.currentProgram(renderProgram);
  });
})();
