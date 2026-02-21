// app.js — Investigation workbench with tabs, global cursor, linked views

const landing = document.getElementById('landing');
const appView = document.getElementById('app-view');
const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');
const loading = document.getElementById('loading');
const loadingMsg = document.getElementById('loading-msg');
const loadingApp = document.getElementById('loading-app');
const loadingMsgApp = document.getElementById('loading-msg-app');
const summaryPanel = document.getElementById('summary-panel');
const summaryTitle = document.getElementById('summary-title');
const summaryContent = document.getElementById('summary-content');
const addFileBtn = document.getElementById('add-file-btn');
const fileInputAdd = document.getElementById('file-input-add');
const toolbarFile = document.getElementById('toolbar-file');
const toolbarCursor = document.getElementById('toolbar-cursor');
const toolbarZoom = document.getElementById('toolbar-zoom');

let worker = null;
let parserSource = null;
let currentTab = 'plots';
const msgCache = new Map();
const MSG_ROW_H = 24;
const scrollPositions = new Map();

const QUICKLOOK_COLORS = ['#2563eb','#c0392b','#1e8449','#6c3483','darkorange','teal','crimson','saddlebrown'];

// Multi-file state
const fileStore = new Map();
let activeFile = null;

// Global cursor state
let cursorT = null;
let cursorLocked = false;
let zoomRange = null;
let selectedMsgIdx = null;
let missionRefTime = null;

// ── Tab switching ──
function showTab(name) {
  currentTab = name;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  const target = document.getElementById('tab-' + name);
  if (target) target.classList.remove('hidden');
  if (name === 'plots') {
    // Trigger Plotly resize after tab becomes visible
    requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
  }
  if (name === 'messages' && activeFile) {
    requestAnimationFrame(() => renderMessages());
  }
}
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => showTab(btn.dataset.tab));
});

// ── Parser loading ──
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
      loadingMsgApp.textContent = msg.msg;
    } else if (msg.type === 'result') {
      loading.classList.add('hidden');
      loadingApp.classList.add('hidden');
      const fname = msg.filename;
      fileStore.set(fname, msg.data);
      activeFile = fname;
      const mrows = buildMessageList(msg.data);
      const types = [...new Set(mrows.map(r => r.type))].sort();
      const enabledTypes = new Set(types);
      msgCache.set(fname, { rows: mrows, filtered: mrows, enabledTypes, searchTerm: '', allTypes: types });
      computeRefTime(msg.data);
      landing.classList.add('hidden');
      appView.classList.remove('hidden');
      toolbarFile.textContent = fname;
      showSummary(msg.data);
      renderAllPlots(msg.data);
      renderQuicklook();
      buildTypeChips();
      applyFilters();
      if (currentTab === 'messages') renderMessages();
    } else if (msg.type === 'error') {
      loading.classList.add('hidden');
      loadingApp.classList.add('hidden');
      if (fileStore.size === 0) landing.classList.remove('hidden');
      alert('Parse error: ' + msg.msg);
    }
  };
}

function computeRefTime(data) {
  const nav = data['Navigation'];
  if (nav && nav.timestamp_ms && nav.timestamp_ms.length > 0) {
    missionRefTime = nav.timestamp_ms[0];
  } else {
    missionRefTime = null;
  }
}

function tHrsToUTC(t) {
  if (t == null) return '--:--:--';
  if (missionRefTime != null) {
    const ms = missionRefTime + t * 3600000;
    const totalSec = (ms / 1000) % 86400;
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = Math.floor(totalSec % 60);
    const frac = Math.floor((ms % 1000));
    return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}.${String(frac).padStart(3,'0')}`;
  }
  const totalSec = t * 3600;
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = Math.floor(totalSec % 60);
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

// ── Summary ──
function showSummary(data) {
  const vehName = (data['Vehicle Name'] && data['Vehicle Name'].name) || 'REMUS-100';
  summaryTitle.textContent = vehName;

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

// ── Build flat message list ──
function buildMessageList(data) {
  const rows = [];
  const skip = new Set(['_raw', '_summary']);
  for (const [type, rec] of Object.entries(data)) {
    if (skip.has(type)) continue;
    if (Array.isArray(rec)) {
      for (const entry of rec) {
        const t = entry.t_hrs != null ? entry.t_hrs : null;
        const fields = {};
        for (const [k, v] of Object.entries(entry)) {
          if (k !== 't_hrs') fields[k] = v;
        }
        rows.push({ t, type, fields });
      }
    } else if (rec && typeof rec === 'object' && rec.t_hrs && Array.isArray(rec.t_hrs)) {
      const keys = Object.keys(rec).filter(k => k !== 't_hrs');
      const n = rec.t_hrs.length;
      for (let i = 0; i < n; i++) {
        const fields = {};
        for (const k of keys) {
          if (Array.isArray(rec[k]) && rec[k].length === n) {
            fields[k] = rec[k][i];
          }
        }
        rows.push({ t: rec.t_hrs[i], type, fields });
      }
    } else if (rec && typeof rec === 'object') {
      const fields = {};
      for (const [k, v] of Object.entries(rec)) {
        if (!Array.isArray(v)) fields[k] = v;
      }
      if (Object.keys(fields).length > 0) {
        rows.push({ t: null, type, fields });
      }
    }
  }
  rows.sort((a, b) => {
    if (a.t == null && b.t == null) return 0;
    if (a.t == null) return -1;
    if (b.t == null) return 1;
    return a.t - b.t;
  });
  for (let i = 0; i < rows.length; i++) rows[i]._idx = i;
  return rows;
}

// ── Type chips ──
function buildTypeChips() {
  const container = document.getElementById('type-chips');
  if (!activeFile || !msgCache.has(activeFile)) { container.innerHTML = ''; return; }
  const cache = msgCache.get(activeFile);
  const types = cache.allTypes;

  container.innerHTML = types.map(type => {
    const color = typeColor(type);
    const on = cache.enabledTypes.has(type);
    return `<span class="type-chip ${on ? 'on' : ''}" data-type="${type}" style="${on ? 'color:' + color + ';border-color:' + color : ''}">` +
      `<span class="chip-dot" style="background:${color}"></span>${type}</span>`;
  }).join('');

  container.querySelectorAll('.type-chip').forEach(chip => {
    chip.addEventListener('click', (e) => {
      const type = chip.dataset.type;
      if (e.shiftKey) {
        // Shift+click: toggle this type on/off
        if (cache.enabledTypes.has(type)) {
          cache.enabledTypes.delete(type);
          if (cache.enabledTypes.size === 0) {
            for (const t of cache.allTypes) cache.enabledTypes.add(t);
          }
        } else {
          cache.enabledTypes.add(type);
        }
      } else {
        // Click: solo this type (or show all if already solo'd)
        const isSolo = cache.enabledTypes.size === 1 && cache.enabledTypes.has(type);
        cache.enabledTypes.clear();
        if (isSolo) {
          for (const t of cache.allTypes) cache.enabledTypes.add(t);
        } else {
          cache.enabledTypes.add(type);
        }
      }
      buildTypeChips();
      applyFilters();
      renderMessages();
    });
  });
}

// ── Message filtering ──
function applyFilters() {
  if (!activeFile || !msgCache.has(activeFile)) return;
  const cache = msgCache.get(activeFile);
  const search = cache.searchTerm.toLowerCase();
  const linkZoom = document.getElementById('link-zoom').checked;

  cache.filtered = cache.rows.filter(r => {
    if (!cache.enabledTypes.has(r.type)) return false;
    if (linkZoom && zoomRange && r.t != null) {
      if (r.t < zoomRange[0] || r.t > zoomRange[1]) return false;
    }
    if (search) {
      const fieldStr = Object.entries(r.fields).map(([k,v]) => `${k}:${v}`).join(' ').toLowerCase();
      if (!fieldStr.includes(search) && !r.type.toLowerCase().includes(search)) return false;
    }
    return true;
  });
}

// Search box
const msgSearch = document.getElementById('msg-search');
let searchDebounce = null;
msgSearch.addEventListener('input', () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    if (!activeFile || !msgCache.has(activeFile)) return;
    msgCache.get(activeFile).searchTerm = msgSearch.value;
    selectedMsgIdx = null;
    applyFilters();
    renderMessages();
  }, 150);
});

document.getElementById('link-zoom').addEventListener('change', () => {
  applyFilters();
  renderMessages();
});

// ── Virtual-scroll messages ──
function renderMessages() {
  if (!activeFile || !msgCache.has(activeFile)) return;
  const cache = msgCache.get(activeFile);
  const rows = cache.filtered;
  const container = document.getElementById('msg-scroll');
  const body = document.getElementById('msg-body');
  const status = document.getElementById('msg-status');

  const total = rows.length;
  const totalAll = cache.rows.length;
  body.style.height = (total * MSG_ROW_H) + 'px';

  status.textContent = total === totalAll
    ? `${total.toLocaleString()} messages`
    : `${total.toLocaleString()} / ${totalAll.toLocaleString()}`;

  if (total === 0) { body.innerHTML = ''; return; }

  function paint() {
    const scrollTop = container.scrollTop;
    const viewH = container.clientHeight;
    const startIdx = Math.max(0, Math.floor(scrollTop / MSG_ROW_H) - 5);
    const endIdx = Math.min(total, Math.ceil((scrollTop + viewH) / MSG_ROW_H) + 5);

    let html = '';
    for (let i = startIdx; i < endIdx; i++) {
      const r = rows[i];
      const tStr = r.t != null ? r.t.toFixed(4) : '--';
      const utcStr = tHrsToUTC(r.t);
      const color = typeColor(r.type);
      const fStr = Object.entries(r.fields).map(([k,v]) => {
        const val = typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(4)) : v;
        return `${k}:${val}`;
      }).join('  ');
      const selected = selectedMsgIdx === i ? ' selected' : '';
      const cursorHl = (!cursorLocked && cursorT != null && r.t != null &&
        Math.abs(r.t - cursorT) < 0.0002) ? ' cursor-highlight' : '';
      html += `<div class="msg-row${selected}${cursorHl}" data-idx="${i}" style="top:${i * MSG_ROW_H}px;height:${MSG_ROW_H}px;border-left-color:${color}">` +
        `<span class="msg-col-time">${tStr}</span>` +
        `<span class="msg-col-utc">${utcStr}</span>` +
        `<span class="msg-col-type">${r.type}</span>` +
        `<span class="msg-col-fields" title="${fStr}">${fStr}</span></div>`;
    }
    body.innerHTML = html;

    body.querySelectorAll('.msg-row').forEach(row => {
      row.addEventListener('click', () => {
        selectMessage(parseInt(row.dataset.idx));
      });
    });
  }

  requestAnimationFrame(() => {
    paint();
    container.onscroll = paint;
  });
}

function selectMessage(idx) {
  if (!activeFile || !msgCache.has(activeFile)) return;
  const cache = msgCache.get(activeFile);
  const rows = cache.filtered;
  if (idx < 0 || idx >= rows.length) return;

  selectedMsgIdx = idx;
  const row = rows[idx];

  if (row.t != null) {
    cursorT = row.t;
    cursorLocked = true;
    updateCursorDisplay();
    updateCursorOnPlots(cursorT);
    updateMapCursor(cursorT);
  }

  showDetailPanel(row, cache.rows);
  renderMessages();
}

// ── Detail panel ──
function showDetailPanel(row, allRows) {
  const panel = document.getElementById('detail-panel');
  const title = document.getElementById('detail-title');
  const fields = document.getElementById('detail-fields');
  const ctxRows = document.getElementById('detail-context-rows');

  const tStr = row.t != null ? row.t.toFixed(4) : '--';
  const utcStr = tHrsToUTC(row.t);
  title.textContent = `${row.type} — t=${tStr}h (${utcStr} UTC) — record #${row._idx}`;

  fields.innerHTML = Object.entries(row.fields).map(([k, v]) => {
    let val;
    if (typeof v === 'number') {
      val = Number.isInteger(v) ? v.toString() : v.toFixed(6);
    } else if (typeof v === 'string') {
      val = v;
    } else {
      val = JSON.stringify(v);
    }
    return `<div class="field-row"><span class="field-key">${k}:</span><span class="field-val">${val}</span></div>`;
  }).join('');

  const rawIdx = row._idx;
  const ctxStart = Math.max(0, rawIdx - 5);
  const ctxEnd = Math.min(allRows.length, rawIdx + 6);
  let ctxHtml = '';
  for (let i = ctxStart; i < ctxEnd; i++) {
    const r = allRows[i];
    const isCurrent = i === rawIdx;
    const color = typeColor(r.type);
    const tS = r.t != null ? r.t.toFixed(4) : '--';
    const summary = Object.entries(r.fields).slice(0, 3).map(([k,v]) => {
      const val = typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(2)) : v;
      return `${k}=${val}`;
    }).join(' ');
    ctxHtml += `<div class="detail-context-row${isCurrent ? ' current' : ''}" data-raw-idx="${i}">` +
      `<span class="ctx-dot" style="background:${color}"></span>` +
      `<span>${r.type}</span><span>${tS}</span><span style="color:var(--muted)">${summary}</span></div>`;
  }
  ctxRows.innerHTML = ctxHtml;

  ctxRows.querySelectorAll('.detail-context-row').forEach(el => {
    el.addEventListener('click', () => {
      const ri = parseInt(el.dataset.rawIdx);
      const cache = msgCache.get(activeFile);
      const filtIdx = cache.filtered.findIndex(r => r._idx === ri);
      if (filtIdx >= 0) {
        selectMessage(filtIdx);
      } else {
        showDetailPanel(cache.rows[ri], cache.rows);
      }
    });
  });

  document.getElementById('detail-hex-content').textContent =
    'Raw payload bytes not available in browser parse.\n' +
    'Field dump:\n' + JSON.stringify(row.fields, null, 2);

  panel.classList.remove('hidden');
}

function hideDetailPanel() {
  document.getElementById('detail-panel').classList.add('hidden');
  selectedMsgIdx = null;
  renderMessages();
}

document.getElementById('detail-close').addEventListener('click', hideDetailPanel);

// ── Global cursor ──
function updateCursorDisplay() {
  if (cursorT != null) {
    const utc = tHrsToUTC(cursorT);
    toolbarCursor.textContent = `Cursor: ${cursorT.toFixed(4)}h (${utc} UTC)`;
  } else {
    toolbarCursor.textContent = 'Cursor: --';
  }
}

function onPlotHover(t) {
  if (cursorLocked) return;
  cursorT = t;
  updateCursorDisplay();
  updateCursorOnPlots(t);
  updateMapCursor(t);
}

function onPlotClick(t) {
  cursorT = t;
  cursorLocked = true;
  updateCursorDisplay();
  updateCursorOnPlots(t);
  updateMapCursor(t);
  scrollMessagesToTime(t);
}

function onPlotZoom(range) {
  zoomRange = range;
  toolbarZoom.textContent = `${range[0].toFixed(3)}h – ${range[1].toFixed(3)}h`;
  if (document.getElementById('link-zoom').checked) {
    applyFilters();
    if (currentTab === 'messages') renderMessages();
  }
}

function onZoomReset() {
  zoomRange = null;
  toolbarZoom.textContent = 'Full range';
  if (document.getElementById('link-zoom').checked) {
    applyFilters();
    if (currentTab === 'messages') renderMessages();
  }
}

document.getElementById('btn-reset-zoom').addEventListener('click', () => {
  resetAllZoom();
});

// ── Map cursor dot ──
function updateMapCursor(t) {
  if (!activeFile) return;
  const data = fileStore.get(activeFile);
  const nav = data && data['Navigation'];
  if (!nav || !nav.t_hrs || !nav.lat || !nav.lon) return;

  const tArr = nav.t_hrs;
  let lo = 0, hi = tArr.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tArr[mid] < t) lo = mid + 1; else hi = mid;
  }
  const lat = nav.lat[lo];
  const lon = nav.lon[lo];

  // Update on both maps
  for (const mapId of ['sidebar-minimap', 'quicklook-map']) {
    const mapEl = document.getElementById(mapId);
    if (!mapEl || !mapEl.data) continue;
    const nTraces = mapEl.data.length;
    const cursorTrace = {
      type: 'scattermapbox', mode: 'markers',
      lat: [lat], lon: [lon],
      marker: { size: 10, color: '#e74c3c', symbol: 'circle' },
      name: 'Cursor', showlegend: false, hoverinfo: 'skip',
    };
    if (mapEl.data[nTraces - 1] && mapEl.data[nTraces - 1].name === 'Cursor') {
      Plotly.restyle(mapEl, { lat: [[lat]], lon: [[lon]] }, [nTraces - 1]);
    } else {
      Plotly.addTraces(mapEl, cursorTrace);
    }
  }
}

function scrollMessagesToTime(t) {
  if (!activeFile || !msgCache.has(activeFile)) return;
  const rows = msgCache.get(activeFile).filtered;
  if (!rows.length) return;

  let lo = 0, hi = rows.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].t == null || rows[mid].t < t) lo = mid + 1; else hi = mid;
  }

  // If not on messages tab, switch to it
  if (currentTab !== 'messages') {
    showTab('messages');
  }

  requestAnimationFrame(() => {
    const container = document.getElementById('msg-scroll');
    container.scrollTop = Math.max(0, (lo - 3) * MSG_ROW_H);
  });
}

// ── File management ──
function switchToFile(fname) {
  if (!fileStore.has(fname)) return;
  const scrollEl = document.getElementById('msg-scroll');
  if (activeFile) scrollPositions.set(activeFile, scrollEl.scrollTop);
  activeFile = fname;
  const data = fileStore.get(fname);
  computeRefTime(data);
  toolbarFile.textContent = fname;
  showSummary(data);
  renderAllPlots(data);
  renderQuicklook();
  buildTypeChips();
  msgSearch.value = msgCache.get(fname)?.searchTerm || '';
  applyFilters();
  if (currentTab === 'messages') renderMessages();
  scrollEl.scrollTop = scrollPositions.get(fname) || 0;
  cursorT = null;
  cursorLocked = false;
  selectedMsgIdx = null;
  zoomRange = null;
  updateCursorDisplay();
  toolbarZoom.textContent = 'Full range';
}

function closeFile(fname) {
  fileStore.delete(fname);
  msgCache.delete(fname);
  if (fileStore.size === 0) {
    activeFile = null;
    summaryPanel.classList.add('hidden');
    appView.classList.add('hidden');
    landing.classList.remove('hidden');
    return;
  }
  if (activeFile === fname) {
    const next = fileStore.keys().next().value;
    switchToFile(next);
  } else {
    renderQuicklook();
  }
}

function renderQuicklook() {
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
        line: { color, width: fname === activeFile ? 3 : 2 },
        name: fname,
        hoverinfo: 'name',
      });
    }
    const dur = (nav && nav.t_hrs) ? (nav.t_hrs[nav.t_hrs.length - 1] - nav.t_hrs[0]).toFixed(2) : '--';
    rows.push({ fname, color, dur });
    i++;
  }

  let allLat = [], allLon = [];
  for (const t of traces) { allLat.push(...t.lat); allLon.push(...t.lon); }
  const cLat = allLat.length ? allLat.reduce((a,b)=>a+b,0)/allLat.length : 21.28;
  const cLon = allLon.length ? allLon.reduce((a,b)=>a+b,0)/allLon.length : -157.84;

  const mapLayout = {
    mapbox: { style: 'open-street-map', center: { lat: cLat, lon: cLon }, zoom: 13 },
    margin: { t: 0, b: 0, l: 0, r: 0 },
    showlegend: false,
  };

  Plotly.react('sidebar-minimap', traces, mapLayout, { responsive: true });
  Plotly.react('quicklook-map', traces,
    Object.assign({}, mapLayout, { showlegend: true, legend: { x: 0.01, y: 0.99, bgcolor: 'rgba(255,255,255,0.8)', font: { size: 10 } } }),
    { responsive: true }
  );

  // Wire map click → time
  const mapEl = document.getElementById('quicklook-map');
  if (mapEl._rlf_clickHandler) {
    mapEl.removeListener('plotly_click', mapEl._rlf_clickHandler);
  }
  mapEl._rlf_clickHandler = function(ev) {
    if (!ev.points || !ev.points[0]) return;
    const pt = ev.points[0];
    const data = fileStore.get(activeFile);
    const nav = data && data['Navigation'];
    if (!nav) return;
    const pIdx = pt.pointIndex;
    if (pIdx != null && nav.t_hrs[pIdx] != null) {
      const t = nav.t_hrs[pIdx];
      cursorT = t;
      cursorLocked = true;
      updateCursorDisplay();
      updateCursorOnPlots(t);
      updateMapCursor(t);
      scrollMessagesToTime(t);
    }
  };
  mapEl.on('plotly_click', mapEl._rlf_clickHandler);

  // Build sidebar table
  const tbody = document.querySelector('#quicklook-table tbody');
  tbody.innerHTML = rows.map(r =>
    `<tr class="${r.fname === activeFile ? 'active' : ''}" data-fname="${r.fname}">
      <td><span class="quicklook-swatch" style="background:${r.color}"></span></td>
      <td>${r.fname}</td><td>${r.dur}</td>
      <td><button class="quicklook-close" data-close="${r.fname}" title="Remove">&times;</button></td>
    </tr>`
  ).join('');
  tbody.querySelectorAll('tr').forEach(tr => {
    tr.addEventListener('click', () => switchToFile(tr.dataset.fname));
  });
  tbody.querySelectorAll('.quicklook-close').forEach(btn => {
    btn.addEventListener('click', (e) => { e.stopPropagation(); closeFile(btn.dataset.close); });
  });
}

function handleFile(file) {
  if (!file || !parserSource) return;
  const fname = file.name;

  if (fileStore.size === 0) {
    loading.classList.remove('hidden');
    loadingMsg.textContent = 'Loading Python runtime...';
  } else {
    loadingApp.classList.remove('hidden');
    loadingMsgApp.textContent = 'Parsing...';
  }

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

// ── Keyboard shortcuts ──
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
    if (e.key === 'Escape') e.target.blur();
    return;
  }

  if (e.key === 'Escape') {
    if (cursorLocked) {
      cursorLocked = false;
      cursorT = null;
      updateCursorDisplay();
      updateCursorOnPlots(null);
    } else if (selectedMsgIdx != null) {
      hideDetailPanel();
    } else {
      document.getElementById('keys-modal').classList.add('hidden');
    }
    return;
  }

  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (currentTab !== 'messages') return;
    e.preventDefault();
    if (!activeFile || !msgCache.has(activeFile)) return;
    const rows = msgCache.get(activeFile).filtered;
    if (!rows.length) return;
    if (selectedMsgIdx == null) {
      selectMessage(0);
    } else {
      const next = e.key === 'ArrowDown'
        ? Math.min(rows.length - 1, selectedMsgIdx + 1)
        : Math.max(0, selectedMsgIdx - 1);
      selectMessage(next);
      const container = document.getElementById('msg-scroll');
      const rowTop = next * MSG_ROW_H;
      if (rowTop < container.scrollTop + MSG_ROW_H) {
        container.scrollTop = Math.max(0, rowTop - MSG_ROW_H);
      } else if (rowTop > container.scrollTop + container.clientHeight - MSG_ROW_H * 2) {
        container.scrollTop = rowTop - container.clientHeight + MSG_ROW_H * 2;
      }
    }
    return;
  }

  if (e.key === 'Enter' && selectedMsgIdx != null) {
    const rows = msgCache.get(activeFile).filtered;
    const row = rows[selectedMsgIdx];
    if (row && row.t != null) {
      cursorT = row.t;
      cursorLocked = true;
      updateCursorDisplay();
      updateCursorOnPlots(cursorT);
      updateMapCursor(cursorT);
    }
    return;
  }

  if (e.key === 'f' || e.key === 'F') {
    e.preventDefault();
    showTab('messages');
    requestAnimationFrame(() => msgSearch.focus());
    return;
  }

  if (e.key === 'r' || e.key === 'R') { resetAllZoom(); return; }
  if (e.key === 'l' || e.key === 'L') {
    const cb = document.getElementById('link-zoom');
    cb.checked = !cb.checked;
    applyFilters();
    if (currentTab === 'messages') renderMessages();
    return;
  }

  if (e.key === '1') { showTab('plots'); return; }
  if (e.key === '2') { showTab('messages'); return; }

  if (e.key === '?') {
    document.getElementById('keys-modal').classList.remove('hidden');
    return;
  }
});

// Keyboard modal
document.querySelector('.toolbar-keys').addEventListener('click', () => {
  document.getElementById('keys-modal').classList.remove('hidden');
});
document.getElementById('keys-modal-close').addEventListener('click', () => {
  document.getElementById('keys-modal').classList.add('hidden');
});
document.getElementById('keys-modal').addEventListener('click', (e) => {
  if (e.target.id === 'keys-modal') e.target.classList.add('hidden');
});

// ── Event listeners — landing upload zone ──
uploadZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); e.target.value = ''; });
uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('dragover');
  if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});

addFileBtn.addEventListener('click', () => fileInputAdd.click());
fileInputAdd.addEventListener('change', (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); e.target.value = ''; });

document.body.addEventListener('dragover', (e) => {
  if (fileStore.size > 0) e.preventDefault();
});
document.body.addEventListener('drop', (e) => {
  if (fileStore.size > 0 && e.dataTransfer.files[0]) {
    e.preventDefault();
    handleFile(e.dataTransfer.files[0]);
  }
});

// Build info
function setBuildInfo(text) {
  for (const id of ['build-info', 'build-info-app']) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
}
(async () => {
  try {
    const r = await fetch('https://api.github.com/repos/isaacgerg/remus-rlf-reader/commits/main');
    if (r.ok) {
      const j = await r.json();
      const sha = j.sha.substring(0, 7).toUpperCase();
      const d = new Date(j.commit.committer.date);
      const dateStr = d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
      const timeStr = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
      setBuildInfo(`Build: ${sha} (${dateStr} ${timeStr})`);
      return;
    }
  } catch (_) {}
  try {
    const r = await fetch('version.txt');
    if (r.ok) { const h = (await r.text()).trim(); if (h) setBuildInfo(`Build: ${h.toUpperCase()}`); }
  } catch (_) {}
})();

// Init
loadParserSource();
initWorker();
