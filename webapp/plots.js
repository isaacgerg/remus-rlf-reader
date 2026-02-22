// plots.js — Plotly-based interactive plots with linked cursors and zoom sync

const COLORS = {
  blue: '#2563eb',
  steel: '#4682b4',
  red: '#c0392b',
  salinity: '#2471a3',
  green: '#1e8449',
  purple: '#6c3483',
  orange: 'darkorange',
  navy: 'navy',
  teal: 'teal',
  brown: 'saddlebrown',
  crimson: 'crimson',
};

// Type-to-color mapping for chips and message row borders
const TYPE_COLORS = {
  'Navigation': '#2563eb',
  'YSI CTD': '#c0392b',
  'ADCP/DVL (1200 kHz)': '#4682b4',
  'Wetlabs ECO BB2F': '#1e8449',
  'Sidescan (900 kHz)': '#6c3483',
  'Battery Status': '#f39c12',
  'Acoustic Modem Log': 'teal',
  'Seabird CTD (SBE49)': 'tomato',
  'Vehicle Name': '#888',
  'Waypoints': 'darkorange',
  'Mission Configuration': '#888',
};
function typeColor(type) {
  return TYPE_COLORS[type] || '#999';
}

const LAYOUT_BASE = {
  margin: { t: 30, r: 45, b: 40, l: 55 },
  font: { size: 10 },
  hovermode: 'x unified',
  paper_bgcolor: 'white',
  plot_bgcolor: '#fafbfc',
};

function merge(base, extra) {
  return Object.assign({}, base, extra);
}

// Downsample arrays for performance
function ds(arr, maxPts) {
  if (!arr || arr.length <= maxPts) return arr;
  const step = Math.ceil(arr.length / maxPts);
  const out = [];
  for (let i = 0; i < arr.length; i += step) out.push(arr[i]);
  return out;
}
const MAX = 5000;

// IDs of all time-series plot divs (for cursor/zoom sync)
const TIME_PLOT_IDS = [
  'plot-depth', 'plot-temp-sal', 'plot-sos', 'plot-eco',
  'plot-attitude', 'plot-nav-speed', 'plot-record-rate', 'plot-dvl',
  'plot-battery', 'plot-modem',
];

// Track which plots have been rendered
const renderedPlots = new Set();

// Cursor line shape template
function cursorShape(t) {
  return {
    type: 'line', x0: t, x1: t, y0: 0, y1: 1,
    xref: 'x', yref: 'paper',
    line: { color: '#e74c3c', width: 1.5, dash: 'dot' },
  };
}

// Update cursor line on all time-series plots
function updateCursorOnPlots(t) {
  for (const id of TIME_PLOT_IDS) {
    const el = document.getElementById(id);
    if (!el || !el.data || !el.data.length) continue;
    const shapes = t != null ? [cursorShape(t)] : [];
    Plotly.relayout(el, { shapes });
  }
}

// Sync x-axis range across all time-series plots
let _syncing = false;
function syncXRange(sourceId, xRange) {
  if (_syncing) return;
  _syncing = true;
  try {
    for (const id of TIME_PLOT_IDS) {
      if (id === sourceId) continue;
      const el = document.getElementById(id);
      if (!el || !el.data || !el.data.length) continue;
      Plotly.relayout(el, { 'xaxis.range': xRange });
    }
  } finally {
    _syncing = false;
  }
}

function resetAllZoom() {
  _syncing = true;
  try {
    for (const id of TIME_PLOT_IDS) {
      const el = document.getElementById(id);
      if (!el || !el.data || !el.data.length) continue;
      Plotly.relayout(el, { 'xaxis.autorange': true, 'yaxis.autorange': true });
    }
  } finally {
    _syncing = false;
  }
  // Notify app.js
  if (typeof window.onZoomReset === 'function') window.onZoomReset();
}

// Wire up hover/click/zoom events on a time-series plot
function wireEvents(divId) {
  const el = document.getElementById(divId);
  if (!el) return;

  // Track double-click timing to suppress the plotly_click that fires during a double-click.
  // Plotly fires plotly_click once per mouse-down release, even when the user double-clicks.
  // When the user double-clicks to reset zoom we must NOT treat it as a cursor lock/navigate.
  let _lastClickTime = 0;
  let _clickSuppressTimer = null;

  el.on('plotly_hover', function(ev) {
    if (ev.points && ev.points[0]) {
      const t = ev.points[0].x;
      if (typeof onPlotHover === 'function') onPlotHover(t);
    }
  });

  el.on('plotly_click', function(ev) {
    if (!ev.points || !ev.points[0]) return;
    const t = ev.points[0].x;
    const now = Date.now();
    const sinceLastClick = now - _lastClickTime;
    _lastClickTime = now;

    // If two plotly_click events arrive within 400 ms it is a double-click.
    // Cancel the pending single-click action and do nothing here — the
    // plotly_relayout double-click handler will call resetAllZoom() instead.
    if (sinceLastClick < 400) {
      clearTimeout(_clickSuppressTimer);
      _clickSuppressTimer = null;
      return;
    }

    // Delay acting on a single click so a fast second click can cancel it.
    _clickSuppressTimer = setTimeout(() => {
      _clickSuppressTimer = null;
      if (typeof onPlotClick === 'function') onPlotClick(t);
    }, 220);
  });

  el.on('plotly_relayout', function(ev) {
    if (_syncing) return;
    if (ev['xaxis.range[0]'] != null && ev['xaxis.range[1]'] != null) {
      const range = [ev['xaxis.range[0]'], ev['xaxis.range[1]']];
      syncXRange(divId, range);
      if (typeof onPlotZoom === 'function') onPlotZoom(range);
    } else if (ev['xaxis.autorange']) {
      // User double-clicked to reset this plot — cancel any pending single-click and reset all.
      clearTimeout(_clickSuppressTimer);
      _clickSuppressTimer = null;
      resetAllZoom();
    }
  });
}

function renderAllPlots(data) {
  renderedPlots.clear();
  plotTrack(data);
  plotDepth(data);
  plotTempSal(data);
  plotSoS(data);
  plotECO(data);
  plotAttitude(data);
  plotNavSpeed(data);
  plotRecordRate(data);
  plotDVL(data);
  plotBattery(data);
  plotModem(data);
  plotSidescan(data);

  // Wire events after render
  for (const id of TIME_PLOT_IDS) {
    if (renderedPlots.has(id)) wireEvents(id);
  }
}

function plotTrack(data) {
  const nav = data['Navigation'];
  if (!nav) return;
  const wps = data['Waypoints'] || [];
  const traces = [{
    x: ds(nav.lon, MAX), y: ds(nav.lat, MAX),
    mode: 'markers', marker: { size: 2, color: ds(nav.depth, MAX), colorscale: 'Plasma', reversescale: true, colorbar: { title: 'Depth (m)', thickness: 12 } },
    type: 'scattergl', name: 'Track', hovertemplate: 'Lon: %{x:.5f}<br>Lat: %{y:.5f}<br>Depth: %{marker.color:.1f} m',
  }];
  if (wps.length) {
    traces.push({
      x: wps.map(w => w.lon), y: wps.map(w => w.lat),
      mode: 'markers', marker: { size: 8, symbol: 'triangle-up', color: 'yellow', line: { width: 1, color: 'black' } },
      name: 'Waypoints',
    });
  }
  Plotly.newPlot('plot-track', traces, merge(LAYOUT_BASE, {
    title: 'AUV Track', xaxis: { title: 'Longitude', scaleanchor: 'y' }, yaxis: { title: 'Latitude' },
    hovermode: 'closest',
  }), { responsive: true });
  renderedPlots.add('plot-track');
}

function plotDepth(data) {
  const nav = data['Navigation'];
  if (!nav) return;
  const t = ds(nav.t_hrs, MAX), d = ds(nav.depth, MAX);
  const traces = [{ x: t, y: d, type: 'scatter', mode: 'lines', fill: 'tozeroy', fillcolor: 'rgba(70,130,180,0.2)', line: { color: COLORS.steel, width: 1 }, name: 'Vehicle' }];
  const adcp = data['ADCP/DVL (1200 kHz)'];
  if (adcp && adcp.depth && adcp.altitude) {
    const n = adcp.depth.length;
    const at = []; const bd = [];
    for (let i = 0; i < n; i++) {
      const alt = adcp.altitude[i];
      if (isFinite(alt) && alt > 0 && alt < 40) {
        at.push(nav.t_hrs[0] + (nav.t_hrs[nav.t_hrs.length-1] - nav.t_hrs[0]) * i / (n-1));
        bd.push(adcp.depth[i] + alt);
      }
    }
    traces.push({ x: ds(at, MAX), y: ds(bd, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.brown, width: 1 }, name: 'Seafloor' });
  }
  Plotly.newPlot('plot-depth', traces, merge(LAYOUT_BASE, {
    title: 'Depth Profile', xaxis: { title: 'Time (hours)' }, yaxis: { title: 'Depth (m)', autorange: 'reversed' },
  }), { responsive: true });
  renderedPlots.add('plot-depth');
}

function plotTempSal(data) {
  const ctd = data['YSI CTD'];
  if (!ctd) return;
  const t = ds(ctd.t_hrs, MAX);
  Plotly.newPlot('plot-temp-sal', [
    { x: t, y: ds(ctd.temperature, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.red, width: 1 }, name: 'Temp (C)', yaxis: 'y' },
    { x: t, y: ds(ctd.salinity, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.salinity, width: 1 }, name: 'Sal (PSU)', yaxis: 'y2' },
  ], merge(LAYOUT_BASE, {
    title: 'Temperature & Salinity',
    xaxis: { title: 'Time (hours)' },
    yaxis: { title: 'Temp (C)', titlefont: { color: COLORS.red }, tickfont: { color: COLORS.red } },
    yaxis2: { title: 'Sal (PSU)', titlefont: { color: COLORS.salinity }, tickfont: { color: COLORS.salinity }, overlaying: 'y', side: 'right' },
    margin: { t: 30, r: 70, b: 40, l: 55 },
  }), { responsive: true });
  renderedPlots.add('plot-temp-sal');
}

function plotSoS(data) {
  const ctd = data['YSI CTD'];
  const sbe = data['Seabird CTD (SBE49)'];
  const traces = [];
  if (ctd && ctd.sound_speed) traces.push({ x: ds(ctd.t_hrs, MAX), y: ds(ctd.sound_speed, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.steel, width: 1 }, name: 'YSI CTD' });
  if (sbe && sbe.sound_speed) traces.push({ x: ds(sbe.t_hrs, MAX), y: ds(sbe.sound_speed, MAX), type: 'scatter', mode: 'lines', line: { color: 'tomato', width: 1.2 }, name: 'SBE49' });
  if (!traces.length) return;
  Plotly.newPlot('plot-sos', traces, merge(LAYOUT_BASE, {
    title: 'Speed of Sound', xaxis: { title: 'Time (hours)' }, yaxis: { title: 'SoS (m/s)' },
  }), { responsive: true });
  renderedPlots.add('plot-sos');
}

function plotECO(data) {
  const eco = data['Wetlabs ECO BB2F'];
  if (!eco || !eco.t_hrs) return;
  const t = ds(eco.t_hrs, MAX);
  Plotly.newPlot('plot-eco', [
    { x: t, y: ds(eco.chlorophyll, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.green, width: 1 }, name: 'Chl (ug/L)', yaxis: 'y' },
    { x: t, y: ds(eco.beta470, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.purple, width: 1 }, name: 'Beta470', yaxis: 'y2' },
  ], merge(LAYOUT_BASE, {
    title: 'Wetlabs ECO BB2F',
    xaxis: { title: 'Time (hours)' },
    yaxis: { title: 'Chl (ug/L)', titlefont: { color: COLORS.green }, tickfont: { color: COLORS.green } },
    yaxis2: { title: 'Beta470 (1/m/sr)', titlefont: { color: COLORS.purple }, tickfont: { color: COLORS.purple }, overlaying: 'y', side: 'right' },
    margin: { t: 30, r: 70, b: 40, l: 55 },
  }), { responsive: true });
  renderedPlots.add('plot-eco');
}

function plotAttitude(data) {
  const adcp = data['ADCP/DVL (1200 kHz)'];
  const nav = data['Navigation'];
  if (!adcp || !nav) return;
  const n = adcp.heading.length;
  const t0 = nav.t_hrs[0], t1 = nav.t_hrs[nav.t_hrs.length - 1];
  const t = []; for (let i = 0; i < n; i++) t.push(t0 + (t1 - t0) * i / (n - 1));
  Plotly.newPlot('plot-attitude', [
    { x: ds(t, MAX), y: ds(adcp.heading, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.navy, width: 1 }, name: 'Heading', yaxis: 'y' },
    { x: ds(t, MAX), y: ds(adcp.pitch, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.orange, width: 1 }, name: 'Pitch', yaxis: 'y2' },
    { x: ds(t, MAX), y: ds(adcp.roll, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.purple, width: 1 }, name: 'Roll', yaxis: 'y2' },
  ], merge(LAYOUT_BASE, {
    title: 'Vehicle Attitude',
    xaxis: { title: 'Time (hours)' },
    yaxis: { title: 'Heading (deg)', titlefont: { color: COLORS.navy }, tickfont: { color: COLORS.navy } },
    yaxis2: { title: 'Pitch/Roll (deg)', overlaying: 'y', side: 'right' },
    margin: { t: 30, r: 70, b: 40, l: 55 },
  }), { responsive: true });
  renderedPlots.add('plot-attitude');
}

function plotNavSpeed(data) {
  const nav = data['Navigation'];
  if (!nav) return;
  const spd = nav.speed.map(s => (s >= 0 && s <= 3) ? s : null);
  Plotly.newPlot('plot-nav-speed', [{
    x: ds(nav.t_hrs, MAX), y: ds(spd, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.teal, width: 1 }, name: 'Speed',
  }], merge(LAYOUT_BASE, {
    title: 'Nav Speed', xaxis: { title: 'Time (hours)' }, yaxis: { title: 'Speed (m/s)', rangemode: 'tozero' },
  }), { responsive: true });
  renderedPlots.add('plot-nav-speed');
}

function plotSidescan(data) {
  const ss = data['Sidescan (900 kHz)'];
  if (!ss) return;
  const lon = [], lat = [], bd = [];
  for (let i = 0; i < ss.depth.length; i++) {
    const a = ss.altitude[i], d = ss.depth[i];
    if (isFinite(a) && isFinite(d) && a > 0 && a < 30) {
      lon.push(ss.lon[i]); lat.push(ss.lat[i]); bd.push(d + a);
    }
  }
  if (!lon.length) return;
  Plotly.newPlot('plot-sidescan', [{
    x: ds(lon, MAX), y: ds(lat, MAX), mode: 'markers',
    marker: { size: 2, color: ds(bd, MAX), colorscale: 'Blues', reversescale: true, colorbar: { title: 'Depth (m)', thickness: 12 } },
    type: 'scattergl', name: 'Bathymetry',
    hovertemplate: 'Lon: %{x:.5f}<br>Lat: %{y:.5f}<br>Depth: %{marker.color:.1f} m',
  }], merge(LAYOUT_BASE, {
    title: 'Sidescan Bathymetry', xaxis: { title: 'Longitude', scaleanchor: 'y' }, yaxis: { title: 'Latitude' },
    hovermode: 'closest',
  }), { responsive: true });
  renderedPlots.add('plot-sidescan');
}

// --- Quality plots ---

function plotRecordRate(data) {
  const nav = data['Navigation'];
  if (!nav) return;
  const tEnd = nav.t_hrs[nav.t_hrs.length - 1];
  const binW = 1 / 60;
  const nBins = Math.ceil(tEnd / binW);
  const centers = []; for (let i = 0; i < nBins; i++) centers.push((i + 0.5) * binW);

  function histogram(tArr) {
    const counts = new Float64Array(nBins);
    for (const t of tArr) { const b = Math.floor(t / binW); if (b >= 0 && b < nBins) counts[b]++; }
    return Array.from(counts);
  }

  const traces = [{ x: centers, y: histogram(nav.t_hrs), type: 'scatter', mode: 'lines', line: { color: COLORS.steel, width: 1 }, name: 'Nav (~18 Hz)' }];
  const ctd = data['YSI CTD'];
  if (ctd) traces.push({ x: centers, y: histogram(ctd.t_hrs), type: 'scatter', mode: 'lines', line: { color: 'tomato', width: 1 }, name: 'CTD (~18 Hz)' });
  const eco = data['Wetlabs ECO BB2F'];
  if (eco && eco.t_hrs) traces.push({ x: centers, y: histogram(eco.t_hrs), type: 'scatter', mode: 'lines', line: { color: COLORS.green, width: 1 }, name: 'ECO (~1 Hz)', yaxis: 'y2' });

  Plotly.newPlot('plot-record-rate', traces, merge(LAYOUT_BASE, {
    title: 'Record Rate', xaxis: { title: 'Time (hours)' },
    yaxis: { title: 'Records/min' },
    yaxis2: eco ? { title: 'ECO rec/min', overlaying: 'y', side: 'right', titlefont: { color: COLORS.green }, tickfont: { color: COLORS.green } } : undefined,
    margin: { t: 30, r: 70, b: 40, l: 55 },
  }), { responsive: true });
  renderedPlots.add('plot-record-rate');
}

function plotDVL(data) {
  const adcp = data['ADCP/DVL (1200 kHz)'];
  const nav = data['Navigation'];
  if (!adcp || !nav) return;
  const n = adcp.altitude.length;
  const tEnd = nav.t_hrs[nav.t_hrs.length - 1];
  const t = []; for (let i = 0; i < n; i++) t.push(tEnd * i / (n - 1));

  const valid = adcp.altitude.map(a => (isFinite(a) && a > 0 && a < 40) ? 1 : 0);
  const win = Math.max(1, Math.round(5 / 60 / tEnd * n));
  const rolling = [];
  let sum = 0;
  for (let i = 0; i < n; i++) {
    sum += valid[i];
    if (i >= win) sum -= valid[i - win];
    rolling.push(sum / Math.min(i + 1, win));
  }

  Plotly.newPlot('plot-dvl', [{
    x: ds(t, MAX), y: ds(rolling, MAX), type: 'scatter', mode: 'lines',
    fill: 'tozeroy', fillcolor: 'rgba(70,130,180,0.15)', line: { color: COLORS.steel, width: 1 },
    name: 'Bottom lock (5-min)',
  }], merge(LAYOUT_BASE, {
    title: 'DVL Bottom Lock',
    xaxis: { title: 'Time (hours)' },
    yaxis: { title: 'Fraction', range: [0, 1.05], tickformat: ',.0%' },
  }), { responsive: true });
  renderedPlots.add('plot-dvl');
}

function plotBattery(data) {
  const batt = data['Battery Status'];
  const nav = data['Navigation'];
  if (!batt || !Array.isArray(batt) || !nav) return;
  const tEnd = nav.t_hrs[nav.t_hrs.length - 1];
  const n = batt.length;

  const groups = {};
  for (let i = 0; i < n; i++) {
    const id = batt[i].batt_id;
    if (!groups[id]) groups[id] = { t: [], v: [] };
    groups[id].t.push(tEnd * i / (n - 1));
    groups[id].v.push((batt[i].pack_mv || 0) / 1000);
  }

  const colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12'];
  const traces = [];
  let ci = 0;
  for (const id of Object.keys(groups).sort()) {
    traces.push({
      x: groups[id].t, y: groups[id].v,
      type: 'scatter', mode: 'lines+markers', marker: { size: 3 },
      line: { color: colors[ci % 4], width: 1 }, name: `Bank ${id}`,
    });
    ci++;
  }

  Plotly.newPlot('plot-battery', traces, merge(LAYOUT_BASE, {
    title: 'Battery Voltage', xaxis: { title: 'Time (hours)' }, yaxis: { title: 'Voltage (V)' },
  }), { responsive: true });
  renderedPlots.add('plot-battery');
}

function plotModem(data) {
  const modem = data['Acoustic Modem Log'];
  const nav = data['Navigation'];
  if (!modem || !modem.message || !modem.t_hrs || !nav) return;
  const qualPat = /Data quality: \(\d+\) (\d+)/;
  const qt = [], qs = [];
  for (let i = 0; i < modem.message.length; i++) {
    const m = qualPat.exec(modem.message[i]);
    if (m) { qt.push(modem.t_hrs[i]); qs.push(parseInt(m[1])); }
  }
  if (!qt.length) return;
  Plotly.newPlot('plot-modem', [{
    x: qt, y: qs, type: 'scatter', mode: 'markers',
    marker: { size: 5, color: COLORS.steel }, name: 'Quality',
  }], merge(LAYOUT_BASE, {
    title: `Modem Quality (n=${qt.length})`,
    xaxis: { title: 'Time (hours)' }, yaxis: { title: 'Score', range: [0, 210] },
    hovermode: 'closest',
  }), { responsive: true });
  renderedPlots.add('plot-modem');
}
