// parse_worker.js — Web Worker that runs Pyodide + remus_rlf.py

let pyodide = null;

async function initPyodide() {
  importScripts('https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js');
  pyodide = await loadPyodide();
  await pyodide.loadPackage('numpy');
  postMessage({ type: 'status', msg: 'Python runtime ready' });
}

const pyodideReady = initPyodide();

onmessage = async function(e) {
  if (e.data.type === 'parse') {
    try {
      await pyodideReady;
      postMessage({ type: 'status', msg: 'Parsing RLF file...' });

      // Make file bytes available to Python via the Pyodide buffer
      const bytes = new Uint8Array(e.data.buffer);
      pyodide.globals.set('_rlf_bytes', bytes);

      // Load remus_rlf.py source and patch it to accept bytes
      const parserSrc = e.data.parserSource;
      pyodide.runPython(parserSrc);

      // Run parse and convert to JSON-safe dict
      pyodide.runPython(`
import json as _json
import math as _math
import numpy as _np

class _SafeEncoder(_json.JSONEncoder):
    def default(self, o):
        if isinstance(o, _np.ndarray):
            return o.tolist()
        if isinstance(o, (_np.integer,)):
            return int(o)
        if isinstance(o, (_np.floating,)):
            v = float(o)
            return None if not _math.isfinite(v) else v
        if isinstance(o, bytes):
            return o.decode('utf-8', errors='replace')
        return super().default(o)

    def encode(self, o):
        return super().encode(self._sanitize(o))

    def _sanitize(self, o):
        if isinstance(o, float):
            return None if not _math.isfinite(o) else o
        if isinstance(o, dict):
            return {k: self._sanitize(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [self._sanitize(v) for v in o]
        if isinstance(o, _np.ndarray):
            return self._sanitize(o.tolist())
        if isinstance(o, (_np.floating,)):
            v = float(o)
            return None if not _math.isfinite(v) else v
        if isinstance(o, (_np.integer,)):
            return int(o)
        if isinstance(o, bytes):
            return o.decode('utf-8', errors='replace')
        return o

def _to_json_safe(result):
    out = {}
    for key, val in result.items():
        if key.startswith('_'):
            continue
        out[key] = val
    return out

# Parse the raw bytes directly
_data = _rlf_bytes.to_py().tobytes()
_raw = parse_raw_records(_data)
_result = {}
_summary = {}
for _rtype, _payloads in _raw.items():
    _name = RECORD_NAMES.get(_rtype, f'Unknown_0x{_rtype:04x}')
    _summary[_name] = {
        'type_hex': f'0x{_rtype:04x}',
        'count': len(_payloads),
        'payload_bytes': len(_payloads[0]) if _payloads else 0,
    }
    _decoder = _DECODERS.get(_rtype)
    if _decoder is not None:
        _result[_name] = _decoder(_payloads)
    else:
        _result[_name] = _payloads

# Inject modem timestamps
_nav_decoded = _result.get('Navigation')
_modem_decoded = _result.get('Acoustic Modem Log')
if (_nav_decoded is not None and _modem_decoded is not None
        and isinstance(_modem_decoded, dict)):
    _modem_decoded['t_hrs'] = _stamp_by_position(
        _data, REC_MODEM_LOG, REC_NAV, _nav_decoded['t_hrs'])

_result['_raw'] = _raw
_result['_summary'] = _summary

import re as _re
_json_result = _json.dumps(_to_json_safe(_result), cls=_SafeEncoder)
_json_result = _re.sub(r'\bNaN\b', 'null', _json_result)
_json_result = _re.sub(r'-?Infinity', 'null', _json_result)
`);

      let jsonStr = pyodide.globals.get('_json_result');
      // Python json.dumps emits NaN/Infinity literals — fix in JS
      jsonStr = jsonStr.replace(/\bNaN\b/g, 'null').replace(/-?Infinity/g, 'null');
      const parsed = JSON.parse(jsonStr);
      postMessage({ type: 'result', data: parsed });
    } catch (err) {
      postMessage({ type: 'error', msg: err.message || String(err) });
    }
  }
};
