// plots.js — Plotly-based interactive plots for parsed RLF data

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

const LAYOUT_BASE = {
  margin: { t: 40, r: 50, b: 50, l: 60 },
  font: { size: 11 },
  hovermode: 'closest',
  paper_bgcolor: 'white',
  plot_bgcolor: '#fafbfc',
};

function merge(base, extra) {
  return Object.assign({}, base, extra);
}

// Downsample arrays to at most maxPts points for performance
function ds(arr, maxPts) {
  if (!arr || arr.length <= maxPts) return arr;
  const step = Math.ceil(arr.length / maxPts);
  const out = [];
  for (let i = 0; i < arr.length; i += step) out.push(arr[i]);
  return out;
}
const MAX = 5000;

function renderAllPlots(data) {
  plotTrack(data);
  plotDepth(data);
  plotTempSal(data);
  plotSoS(data);
  plotECO(data);
  plotAttitude(data);
  plotSidescan(data);
  plotNavSpeed(data);
  plotRecordRate(data);
  plotDVL(data);
  plotBattery(data);
  plotModem(data);
}

function plotTrack(data) {
  const nav = data['Navigation'];
  if (!nav) return;
  const wps = data['Waypoints'] || [];
  const traces = [{
    x: ds(nav.lon, MAX), y: ds(nav.lat, MAX),
    mode: 'markers', marker: { size: 2, color: ds(nav.depth, MAX), colorscale: 'Plasma', reversescale: true, colorbar: { title: 'Depth (m)', thickness: 15 } },
    type: 'scattergl', name: 'Track', hovertemplate: 'Lon: %{x:.5f}<br>Lat: %{y:.5f}<br>Depth: %{marker.color:.1f} m',
  }];
  if (wps.length) {
    traces.push({
      x: wps.map(w => w.lon), y: wps.map(w => w.lat),
      mode: 'markers', marker: { size: 10, symbol: 'triangle-up', color: 'yellow', line: { width: 1, color: 'black' } },
      name: 'Waypoints',
    });
  }
  Plotly.newPlot('plot-track', traces, merge(LAYOUT_BASE, {
    title: 'AUV Track', xaxis: { title: 'Longitude', scaleanchor: 'y' }, yaxis: { title: 'Latitude' },
  }), { responsive: true });
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
}

function plotTempSal(data) {
  const ctd = data['YSI CTD'];
  if (!ctd) return;
  const t = ds(ctd.t_hrs, MAX);
  Plotly.newPlot('plot-temp-sal', [
    { x: t, y: ds(ctd.temperature, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.red, width: 1 }, name: 'Temperature (C)', yaxis: 'y' },
    { x: t, y: ds(ctd.salinity, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.salinity, width: 1 }, name: 'Salinity (PSU)', yaxis: 'y2' },
  ], merge(LAYOUT_BASE, {
    title: 'Temperature & Salinity (YSI CTD)',
    xaxis: { title: 'Time (hours)' },
    yaxis: { title: 'Temperature (C)', titlefont: { color: COLORS.red }, tickfont: { color: COLORS.red } },
    yaxis2: { title: 'Salinity (PSU)', titlefont: { color: COLORS.salinity }, tickfont: { color: COLORS.salinity }, overlaying: 'y', side: 'right' },
    margin: { t: 40, r: 80, b: 50, l: 60 },
  }), { responsive: true });
}

function plotSoS(data) {
  const ctd = data['YSI CTD'];
  const sbe = data['Seabird CTD (SBE49)'];
  const traces = [];
  if (ctd && ctd.sound_speed) traces.push({ x: ds(ctd.t_hrs, MAX), y: ds(ctd.sound_speed, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.steel, width: 1 }, name: 'YSI CTD' });
  if (sbe && sbe.sound_speed) traces.push({ x: ds(sbe.t_hrs, MAX), y: ds(sbe.sound_speed, MAX), type: 'scatter', mode: 'lines', line: { color: 'tomato', width: 1.2 }, name: 'Seabird SBE49' });
  if (!traces.length) return;
  Plotly.newPlot('plot-sos', traces, merge(LAYOUT_BASE, {
    title: 'Speed of Sound', xaxis: { title: 'Time (hours)' }, yaxis: { title: 'Speed of Sound (m/s)' },
  }), { responsive: true });
}

function plotECO(data) {
  const eco = data['Wetlabs ECO BB2F'];
  if (!eco || !eco.t_hrs) return;
  const t = ds(eco.t_hrs, MAX);
  Plotly.newPlot('plot-eco', [
    { x: t, y: ds(eco.chlorophyll, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.green, width: 1 }, name: 'Chlorophyll (ug/L)', yaxis: 'y' },
    { x: t, y: ds(eco.beta470, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.purple, width: 1 }, name: 'Beta470 (1/m/sr)', yaxis: 'y2' },
  ], merge(LAYOUT_BASE, {
    title: 'Wetlabs ECO BB2F',
    xaxis: { title: 'Time (hours)' },
    yaxis: { title: 'Chlorophyll (ug/L)', titlefont: { color: COLORS.green }, tickfont: { color: COLORS.green } },
    yaxis2: { title: 'Beta470 (1/m/sr)', titlefont: { color: COLORS.purple }, tickfont: { color: COLORS.purple }, overlaying: 'y', side: 'right' },
    margin: { t: 40, r: 80, b: 50, l: 60 },
  }), { responsive: true });
}

function plotAttitude(data) {
  const adcp = data['ADCP/DVL (1200 kHz)'];
  const nav = data['Navigation'];
  if (!adcp || !nav) return;
  const n = adcp.heading.length;
  const t0 = nav.t_hrs[0], t1 = nav.t_hrs[nav.t_hrs.length - 1];
  const t = []; for (let i = 0; i < n; i++) t.push(t0 + (t1 - t0) * i / (n - 1));
  Plotly.newPlot('plot-attitude', [
    { x: ds(t, MAX), y: ds(adcp.heading, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.navy, width: 1 }, name: 'Heading (deg)', yaxis: 'y' },
    { x: ds(t, MAX), y: ds(adcp.pitch, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.orange, width: 1 }, name: 'Pitch (deg)', yaxis: 'y2' },
    { x: ds(t, MAX), y: ds(adcp.roll, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.purple, width: 1 }, name: 'Roll (deg)', yaxis: 'y2' },
  ], merge(LAYOUT_BASE, {
    title: 'Vehicle Attitude',
    xaxis: { title: 'Time (hours)' },
    yaxis: { title: 'Heading (deg)', titlefont: { color: COLORS.navy }, tickfont: { color: COLORS.navy } },
    yaxis2: { title: 'Pitch / Roll (deg)', overlaying: 'y', side: 'right' },
    margin: { t: 40, r: 80, b: 50, l: 60 },
  }), { responsive: true });
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
    marker: { size: 2, color: ds(bd, MAX), colorscale: 'Blues', reversescale: true, colorbar: { title: 'Water Depth (m)', thickness: 15 } },
    type: 'scattergl', name: 'Bathymetry',
    hovertemplate: 'Lon: %{x:.5f}<br>Lat: %{y:.5f}<br>Depth: %{marker.color:.1f} m',
  }], merge(LAYOUT_BASE, {
    title: 'Sidescan Bathymetry', xaxis: { title: 'Longitude', scaleanchor: 'y' }, yaxis: { title: 'Latitude' },
  }), { responsive: true });
}

function plotNavSpeed(data) {
  const nav = data['Navigation'];
  if (!nav) return;
  // Clip speed to 0-3 m/s
  const spd = nav.speed.map(s => (s >= 0 && s <= 3) ? s : null);
  Plotly.newPlot('plot-nav-speed', [{
    x: ds(nav.t_hrs, MAX), y: ds(spd, MAX), type: 'scatter', mode: 'lines', line: { color: COLORS.teal, width: 1 }, name: 'Speed',
  }], merge(LAYOUT_BASE, {
    title: 'Navigation Speed', xaxis: { title: 'Time (hours)' }, yaxis: { title: 'Speed (m/s)', rangemode: 'tozero' },
  }), { responsive: true });
}

// --- Quality plots ---

function plotRecordRate(data) {
  const nav = data['Navigation'];
  if (!nav) return;
  const tEnd = nav.t_hrs[nav.t_hrs.length - 1];
  const binW = 1 / 60; // 1-minute bins
  const nBins = Math.ceil(tEnd / binW);
  const centers = []; for (let i = 0; i < nBins; i++) centers.push((i + 0.5) * binW);

  function histogram(tArr) {
    const counts = new Float64Array(nBins);
    for (const t of tArr) { const b = Math.floor(t / binW); if (b >= 0 && b < nBins) counts[b]++; }
    return Array.from(counts);
  }

  const traces = [{ x: centers, y: histogram(nav.t_hrs), type: 'scatter', mode: 'lines', line: { color: COLORS.steel, width: 1 }, name: 'Navigation (~18 Hz)' }];
  const ctd = data['YSI CTD'];
  if (ctd) traces.push({ x: centers, y: histogram(ctd.t_hrs), type: 'scatter', mode: 'lines', line: { color: 'tomato', width: 1 }, name: 'YSI CTD (~18 Hz)' });
  const eco = data['Wetlabs ECO BB2F'];
  if (eco && eco.t_hrs) traces.push({ x: centers, y: histogram(eco.t_hrs), type: 'scatter', mode: 'lines', line: { color: COLORS.green, width: 1 }, name: 'ECO BB2F (~1 Hz)', yaxis: 'y2' });

  Plotly.newPlot('plot-record-rate', traces, merge(LAYOUT_BASE, {
    title: 'Sensor Record Rate', xaxis: { title: 'Time (hours)' },
    yaxis: { title: 'Records per Minute' },
    yaxis2: eco ? { title: 'ECO Records/min', overlaying: 'y', side: 'right', titlefont: { color: COLORS.green }, tickfont: { color: COLORS.green } } : undefined,
    margin: { t: 40, r: 80, b: 50, l: 60 },
  }), { responsive: true });
}

function plotDVL(data) {
  const adcp = data['ADCP/DVL (1200 kHz)'];
  const nav = data['Navigation'];
  if (!adcp || !nav) return;
  const n = adcp.altitude.length;
  const tEnd = nav.t_hrs[nav.t_hrs.length - 1];
  const t = []; for (let i = 0; i < n; i++) t.push(tEnd * i / (n - 1));

  // Rolling 5-min bottom lock fraction
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
    name: 'DVL bottom lock (5-min rolling)',
  }], merge(LAYOUT_BASE, {
    title: 'DVL Bottom Lock & Acoustic Navigation Fixes',
    xaxis: { title: 'Time (hours)' },
    yaxis: { title: 'Bottom Lock Fraction', range: [0, 1.05], tickformat: ',.0%' },
  }), { responsive: true });
}

function plotBattery(data) {
  const batt = data['Battery Status'];
  const nav = data['Navigation'];
  if (!batt || !Array.isArray(batt) || !nav) return;
  const tEnd = nav.t_hrs[nav.t_hrs.length - 1];
  const n = batt.length;

  // Group by batt_id
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
      type: 'scatter', mode: 'lines+markers', marker: { size: 4 },
      line: { color: colors[ci % 4], width: 1.2 }, name: `Bank ${id}`,
    });
    ci++;
  }

  Plotly.newPlot('plot-battery', traces, merge(LAYOUT_BASE, {
    title: 'Smart Battery Pack Voltage', xaxis: { title: 'Time (hours, approx.)' }, yaxis: { title: 'Pack Voltage (V)' },
  }), { responsive: true });
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
    marker: { size: 6, color: COLORS.steel }, name: 'Quality Score',
  }], merge(LAYOUT_BASE, {
    title: `Acoustic Modem Receive Quality (n=${qt.length})`,
    xaxis: { title: 'Time (hours)' }, yaxis: { title: 'Quality Score', range: [0, 210] },
  }), { responsive: true });
}
