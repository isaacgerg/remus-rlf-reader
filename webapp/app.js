// app.js — Orchestrates file upload, worker, and plot rendering

const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');
const loading = document.getElementById('loading');
const loadingMsg = document.getElementById('loading-msg');
const summaryPanel = document.getElementById('summary-panel');
const summaryTitle = document.getElementById('summary-title');
const summaryContent = document.getElementById('summary-content');
const plotsContainer = document.getElementById('plots-container');

let worker = null;
let parserSource = null;

// Load remus_rlf.py source at startup
async function loadParserSource() {
  // Try loading from sibling directory (typical deployment alongside remus_rlf.py)
  for (const path of ['../remus_rlf.py', './remus_rlf.py']) {
    try {
      const resp = await fetch(path);
      if (resp.ok) {
        parserSource = await resp.text();
        // Strip the __main__ block to avoid side effects
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
      showSummary(msg.data);
      plotsContainer.classList.remove('hidden');
      renderAllPlots(msg.data);
    } else if (msg.type === 'error') {
      loading.classList.add('hidden');
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

function handleFile(file) {
  if (!file || !parserSource) return;
  uploadZone.classList.add('hidden');
  loading.classList.remove('hidden');
  loadingMsg.textContent = 'Loading Python runtime...';

  const reader = new FileReader();
  reader.onload = function(ev) {
    worker.postMessage({
      type: 'parse',
      buffer: ev.target.result,
      parserSource: parserSource,
    }, [ev.target.result]);
  };
  reader.readAsArrayBuffer(file);
}

// Event listeners
uploadZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); });
uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('dragover');
  if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});

// Init
loadParserSource();
initWorker();
