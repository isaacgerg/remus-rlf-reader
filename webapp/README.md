# REMUS RLF Web Explorer

A 100% client-side web app for exploring REMUS-100 AUV Run Log File (.RLF) data interactively in the browser. No server required — all parsing runs locally via [Pyodide](https://pyodide.org/) (CPython compiled to WebAssembly).

## Quick Start

```bash
cd webapp
python -m http.server 8000
# Open http://localhost:8000
```

Then drag and drop an `.RLF` file onto the page.

## How It Works

1. **Pyodide** loads a full CPython + NumPy runtime in a Web Worker
2. `remus_rlf.py` is fetched and executed inside that worker (no modifications needed)
3. Parsed data is serialized to JSON and sent to the main thread
4. **Plotly.js** renders interactive plots with zoom, pan, and hover tooltips

## Plots

**Summary (8 panels):** Track map, depth profile, temperature & salinity, speed of sound, ECO chlorophyll & backscatter, vehicle attitude, sidescan bathymetry, navigation speed.

**Data Quality (4 panels):** Sensor record rate, DVL bottom lock, battery voltage, acoustic modem quality.

## Requirements

- A modern browser (Chrome, Firefox, Edge, Safari)
- The `remus_rlf.py` file must be accessible at `../remus_rlf.py` relative to the webapp directory
- No install, no build step, no API keys

## Architecture

```
index.html          Main page with upload UI and plot containers
style.css           Styling (responsive grid)
app.js              Orchestrates file upload, worker communication, plot rendering
parse_worker.js     Web Worker: loads Pyodide + remus_rlf.py, parses RLF bytes
plots.js            Plotly.js plot functions (mirrors the matplotlib plots)
```
