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
import numpy as _np

def _to_json_safe(result):
    out = {}
    for key, val in result.items():
        if key.startswith('_'):
            continue
        if isinstance(val, dict):
            safe = {}
            for k, v in val.items():
                if hasattr(v, 'tolist'):
                    if hasattr(v, 'dtype') and v.dtype.kind == 'f':
                        safe[k] = [None if not _np.isfinite(x) else x for x in v.flat] if v.ndim <= 1 else v.tolist()
                    else:
                        safe[k] = v.tolist()
                elif isinstance(v, bytes):
                    safe[k] = v.decode('utf-8', errors='replace')
                else:
                    safe[k] = v
            out[key] = safe
        elif isinstance(val, list):
            # List of dicts (waypoints, battery status, etc.)
            safe_list = []
            for item in val:
                if isinstance(item, dict):
                    safe_item = {}
                    for k, v in item.items():
                        if hasattr(v, 'tolist'):
                            safe_item[k] = v.tolist()
                        elif isinstance(v, bytes):
                            safe_item[k] = v.decode('utf-8', errors='replace')
                        else:
                            safe_item[k] = v
                    safe_list.append(safe_item)
                elif isinstance(item, bytes):
                    safe_list.append(item.decode('utf-8', errors='replace'))
                else:
                    safe_list.append(item)
            out[key] = safe_list
        elif isinstance(val, bytes):
            out[key] = val.decode('utf-8', errors='replace')
        else:
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

_json_result = _json.dumps(_to_json_safe(_result))
`);

      const jsonStr = pyodide.globals.get('_json_result');
      const parsed = JSON.parse(jsonStr);
      postMessage({ type: 'result', data: parsed });
    } catch (err) {
      postMessage({ type: 'error', msg: err.message || String(err) });
    }
  }
};
