// app.js — Orchestrates file upload, worker, and plot rendering

const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');
const loading = document.getElementById('loading');
const loadingMsg = document.getElementById('loading-msg');
const summaryPanel = document.getElementById('summary-panel');
const summaryTitle = document.getElementById('summary-title');
const summaryContent = document.getElementById('summary-content');
const plotsContainer = document.getElementById('plots-container');
const fileTabs = document.getElementById('file-tabs');
const tabList = document.getElementById('tab-list');
const addFileBtn = document.getElementById('add-file-btn');
const fileInputTabs = document.getElementById('file-input-tabs');

let worker = null;
let parserSource = null;

const QUICKLOOK_COLORS = ['#2563eb','#c0392b','#1e8449','#6c3483','darkorange','teal','crimson','saddlebrown'];

// Multi-file state
const fileStore = new Map(); // filename -> parsed data
let activeFile = null;

// Load remus_rlf.py source at startup
async function loadParserSource() {
  for (const path of ['../remus_rlf.py', './remus_rlf.py']) {
    try {
      const resp = await fetch(path);
      if (resp.ok) {
        parserSource = await resp.text();
        const mainIdx = parserSource.indexOf("\nif __name__");
        if (mainIdx > -1) parserSource = parserSource.substring(0, mainIdx);
        return;
      }
    } catch (_) {}
  }
  alert('Could not load remus_rlf.py. Make sure it is accessible at ../remus_rlf.py');
}

function initWorker() {
  worker = new Worker('parse_worker.js?v=' + Date.now());
  worker.onmessage = function(e) {
    const msg = e.data;
    if (msg.type === 'status') {
      loadingMsg.textContent = msg.msg;
    } else if (msg.type === 'result') {
      loading.classList.add('hidden');
      const fname = msg.filename;
      fileStore.set(fname, msg.data);
      activeFile = fname;
      renderTabs();
      fileTabs.classList.remove('hidden');
      uploadZone.classList.add('hidden');
      showSummary(msg.data);
      plotsContainer.classList.remove('hidden');
      renderAllPlots(msg.data);
      renderQuicklook();
    } else if (msg.type === 'error') {
      loading.classList.add('hidden');
      // Show upload zone again if no files loaded
      if (fileStore.size === 0) uploadZone.classList.remove('hidden');
      alert('Parse error: ' + msg.msg);
    }
  };
}

function showSummary(data) {
  const vehName = (data['Vehicle Name'] && data['Vehicle Name'].name) || 'REMUS-100';
  summaryTitle.textContent = `Mission Summary — ${vehName}`;

  const nav = data['Navigation'];
  const items = [];
  if (nav && nav.t_hrs) {
    const dur = nav.t_hrs[nav.t_hrs.length - 1] - nav.t_hrs[0];
    items.push(['Duration', `${dur.toFixed(2)} hours`]);
    items.push(['Nav records', nav.t_hrs.length.toLocaleString()]);
  }
  const ctd = data['YSI CTD'];
  if (ctd) items.push(['CTD records', ctd.t_hrs.length.toLocaleString()]);
  const eco = data['Wetlabs ECO BB2F'];
  if (eco && eco.t_hrs) items.push(['ECO records', eco.t_hrs.length.toLocaleString()]);
  const adcp = data['ADCP/DVL (1200 kHz)'];
  if (adcp) items.push(['ADCP records', adcp.heading.length.toLocaleString()]);
  const ss = data['Sidescan (900 kHz)'];
  if (ss) items.push(['Sidescan records', ss.depth.length.toLocaleString()]);
  const batt = data['Battery Status'];
  if (batt && Array.isArray(batt)) items.push(['Battery records', batt.length.toLocaleString()]);

  summaryContent.innerHTML = items.map(([label, value]) =>
    `<div class="summary-item"><span class="label">${label}</span><span class="value">${value}</span></div>`
  ).join('');
  summaryPanel.classList.remove('hidden');
}

function renderTabs() {
  tabList.innerHTML = '';
  for (const fname of fileStore.keys()) {
    const tab = document.createElement('div');
    tab.className = 'file-tab' + (fname === activeFile ? ' active' : '');

    const label = document.createElement('span');
    label.textContent = fname;
    label.addEventListener('click', () => switchToFile(fname));

    const closeBtn = document.createElement('button');
    closeBtn.className = 'tab-close';
    closeBtn.textContent = '\u00d7';
    closeBtn.addEventListener('click', (e) => { e.stopPropagation(); closeFile(fname); });

    tab.appendChild(label);
    tab.appendChild(closeBtn);
    tabList.appendChild(tab);
  }
}

function switchToFile(fname) {
  if (!fileStore.has(fname)) return;
  activeFile = fname;
  renderTabs();
  const data = fileStore.get(fname);
  showSummary(data);
  renderAllPlots(data);
  renderQuicklook();
}

function closeFile(fname) {
  fileStore.delete(fname);
  if (fileStore.size === 0) {
    activeFile = null;
    fileTabs.classList.add('hidden');
    summaryPanel.classList.add('hidden');
    plotsContainer.classList.add('hidden');
    uploadZone.classList.remove('hidden');
    renderQuicklook();
    return;
  }
  if (activeFile === fname) {
    // Switch to first remaining file
    const next = fileStore.keys().next().value;
    switchToFile(next);
  } else {
    renderTabs();
    renderQuicklook();
  }
}

function renderQuicklook() {
  const panel = document.getElementById('quicklook-panel');
  if (fileStore.size === 0) { panel.classList.add('hidden'); return; }
  panel.classList.remove('hidden');

  // Build Plotly traces
  const traces = [];
  const rows = [];
  let i = 0;
  for (const [fname, data] of fileStore) {
    const color = QUICKLOOK_COLORS[i % QUICKLOOK_COLORS.length];
    const nav = data['Navigation'];
    if (nav && nav.lat && nav.lon) {
      const lat = ds(nav.lat, 2000);
      const lon = ds(nav.lon, 2000);
      traces.push({
        type: 'scattermapbox', mode: 'lines',
        lat, lon,
        line: { color, width: 3 },
        name: fname,
        hoverinfo: 'name',
      });
    }
    const dur = (nav && nav.t_hrs) ? (nav.t_hrs[nav.t_hrs.length - 1] - nav.t_hrs[0]).toFixed(2) : '—';
    const cnt = (nav && nav.t_hrs) ? nav.t_hrs.length.toLocaleString() : '—';
    rows.push({ fname, color, dur, cnt });
    i++;
  }

  // Compute center from all traces
  let allLat = [], allLon = [];
  for (const t of traces) { allLat.push(...t.lat); allLon.push(...t.lon); }
  const cLat = allLat.length ? allLat.reduce((a,b)=>a+b,0)/allLat.length : 21.28;
  const cLon = allLon.length ? allLon.reduce((a,b)=>a+b,0)/allLon.length : -157.84;

  Plotly.react('quicklook-map', traces, {
    mapbox: { style: 'open-street-map', center: { lat: cLat, lon: cLon }, zoom: 13 },
    margin: { t: 0, b: 0, l: 0, r: 0 },
    showlegend: true,
    legend: { x: 0.01, y: 0.99, bgcolor: 'rgba(255,255,255,0.8)', font: { size: 11 } },
  }, { responsive: true });

  // Build table
  const tbody = document.querySelector('#quicklook-table tbody');
  tbody.innerHTML = rows.map(r =>
    `<tr class="${r.fname === activeFile ? 'active' : ''}" data-fname="${r.fname}">
      <td><span class="quicklook-swatch" style="background:${r.color}"></span></td>
      <td>${r.fname}</td><td>${r.dur}</td><td>${r.cnt}</td>
    </tr>`
  ).join('');
  tbody.querySelectorAll('tr').forEach(tr => {
    tr.addEventListener('click', () => switchToFile(tr.dataset.fname));
  });
}

function handleFile(file) {
  if (!file || !parserSource) return;
  loading.classList.remove('hidden');
  uploadZone.classList.add('hidden');
  loadingMsg.textContent = 'Loading Python runtime...';

  const fname = file.name;
  const reader = new FileReader();
  reader.onload = function(ev) {
    worker.postMessage({
      type: 'parse',
      buffer: ev.target.result,
      parserSource: parserSource,
      filename: fname,
    }, [ev.target.result]);
  };
  reader.readAsArrayBuffer(file);
}

// Event listeners — main upload zone
uploadZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); e.target.value = ''; });
uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('dragover');
  if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});

// Event listeners — tab bar add button
addFileBtn.addEventListener('click', () => fileInputTabs.click());
fileInputTabs.addEventListener('change', (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); e.target.value = ''; });

// Allow drag-drop on the whole page when files are already loaded
document.body.addEventListener('dragover', (e) => {
  if (fileStore.size > 0) e.preventDefault();
});
document.body.addEventListener('drop', (e) => {
  if (fileStore.size > 0 && e.dataTransfer.files[0]) {
    e.preventDefault();
    handleFile(e.dataTransfer.files[0]);
  }
});

// Init
loadParserSource();
initWorker();
