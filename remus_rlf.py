"""
REMUS RLF (Run Log File) Parser
================================
Binary format specification and parser for REMUS-100 AUV
Run Log Files (.RLF).

Vehicle: REMUS-100 "Aukai" (SN 256, Hydroid Inc.)
Dataset: Makua Beach, O'ahu, Hawaii — Sep 6-8, 2013
Source:  doi:10.6075/J09P3042 (UCSD Library)
Ref:    Amador et al. (2020), JGR Oceans, 10.1029/2020JC016264

FILE STRUCTURE
--------------
Sequential binary records, each framed by an 8-byte header:

    Offset  Size   Type        Description
    ------  ----   ----        -----------
    0       2      uint8[2]    Magic bytes: 0xEB 0x90
    2       2      uint16 LE   Checksum
    4       2      uint16 LE   Record type
    6       2      uint16 LE   Payload length (bytes)
    8       N      bytes       Payload data

TIMESTAMP FORMAT
----------------
Most records carry a uint32 timestamp at payload offset 16:
  - Units: milliseconds since midnight UTC
  - Bit 31 is a flag (mask with 0x7FFFFFFF for the time value)
  - Wraps at midnight (subtract ~86,400,000 ms to detect; add back to unwrap)
  - Navigation/CTD records: ~18 Hz (~55 ms between samples)
  - ADCP records: ~0.67 Hz (~1.5 s between samples)

RECORD TYPES
------------
Type     Dec   Name                  Payload  Approx Rate   Description
----     ---   ----                  -------  -----------   -----------
0x044e   1102  Navigation            46 B     ~18 Hz        Lat, lon, depth, speed, pitch
0x041d   1053  YSI CTD               40 B     ~18 Hz        Conductivity, temp, salinity, SoS
0x040a   1034  Seabird CTD (SBE49)   32 B     ~0.3 Hz       Conductivity, temp, salinity, SoS
0x03e8   1000  ADCP/DVL (1200 kHz)   155 B    ~0.35 Hz      Attitude, depth, altitude, 3 positions
0x03f7   1015  Sidescan (900 kHz)    55 B     ~1.3 Hz       Lat, lon, altitude, depth, heading
0x043e   1086  Wetlabs ECO BB2F      57 B     ~1 Hz         Optical backscatter, chlorophyll
0x03f9   1017  GPS / Acoustic Nav    59 B     varies        Position fixes, transponder IDs
0x0424   1060  Acoustic Modem Log    37 B     ~0.16 Hz      Inbound/outbound acoustic messages
0x041a   1050  Nav/Acoustic          57 B     ~0.15 Hz      Heading, SoS, position (DVL + compass)
0x0402   1026  Energy Monitor        13 B     ~0.06 Hz      Battery capacity and energy consumed
0x03f1   1009  Objective Navigation  53 B     ~0.03 Hz      Leg progress: FROM/TO, RPM, speed, mode
0x0415   1045  Compass Calibration   48 B     ~0.01 Hz      Heading bias measurements per ref heading
0x040e   1038  Housing Temperature   48 B     ~0.02 Hz      Housing temp and compass error FIFO
0x040b   1035  DVL Status            60 B     sparse        ADCP/DVL subsystem diagnostics (raw hex)
0x0408   1032  Subsystem Mode         6 B     sparse        Mode/status flag register (raw hex)
0x0446   1094  Startup Flag           4 B     startup       Constant marker (10 per mission)
0x03ef   1007  Event Marker           0 B     sparse        Empty-payload phase transition marker

SENSOR CROSS-REFERENCE (from 130906.ini config)
------------------------------------------------
- YSI CTD:           Present, ~18 Hz
- Seabird CTD:       SBE49, present, ~0.3 Hz
- ADCP/DVL:          RDI 1200 kHz, upward + downward looking, 1 m bins
- Sidescan:          MSTL 900 kHz
- Altimeter:         Imagenex 852, max range 10 m, 18 Hz
- Wetlabs ECO BB2F:  Ref470, Beta470, Ref650, Beta650, Chl-a, Thermistor
- GPS:               NMEA 183, UTC offset -600 min (HST)
- Compass:           With gyro chip, auto declination
- Modem:             Acoustic, transponder IDs in GPS records
- Batteries:         4× Lead Acid, ~300 Wh
"""

import struct
import numpy as np


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAGIC = b'\xEB\x90'
HEADER_SIZE = 8  # magic(2) + checksum(2) + type(2) + length(2)

# Record type IDs
REC_NAV           = 0x044e  # Navigation
REC_CTD_YSI       = 0x041d  # YSI CTD
REC_CTD_SBE       = 0x040a  # Seabird CTD (SBE49)
REC_ADCP          = 0x03e8  # ADCP / DVL (1200 kHz)
REC_SIDESCAN      = 0x03f7  # MSTL Sidescan (900 kHz)
REC_ECO           = 0x043e  # Wetlabs ECO BB2F
REC_GPS           = 0x03f9  # GPS / Acoustic Nav
REC_VEHICLE_NAME  = 0x03f4  # Vehicle name string
REC_VEHICLE_INFO  = 0x040d  # Vehicle startup info log
REC_MANUFACTURER  = 0x0416  # Manufacturer info string
REC_MODEM_LOG     = 0x0424  # Acoustic modem communication log
REC_DIAGNOSTIC    = 0x03e9  # Firmware diagnostic / warning log
REC_MISSION_MODES = 0x03ee  # Mission mode type lookup table
REC_MISSION_LEGS  = 0x03f0  # Mission leg / objective waypoints
REC_SENSOR_NAMES  = 0x03fc  # Sensor name strings
REC_SENSOR_TYPES  = 0x0407  # Sensor type ID to name mapping
REC_SENSOR_DISPLAY = 0x040c # Sensor display format configuration
REC_NAV_ACOUSTIC  = 0x041a  # Navigation / acoustic positioning data
REC_DATA_CHANNELS = 0x041c  # Internal data type channel definitions
REC_WAYPOINTS     = 0x0427  # Mission waypoints with lat/lon
REC_ECO_CAL       = 0x043d  # ECO BB2F sensor channel calibration
REC_ACOUSTIC_FIX  = 0x041f  # Acoustic transponder navigation fix
REC_BATTERY_STATUS = 0x0412 # Smart battery status (voltages, chemistry)
REC_BATTERY_CELLS  = 0x0413 # Smart battery cell-level data
REC_OBJ_NAV       = 0x03f1  # Objective Navigation (mission leg progress)
REC_COMPASS_CAL   = 0x0415  # Compass Calibration
REC_HOUSING_TEMP  = 0x040e  # Housing Temperature
REC_ENERGY_MON    = 0x0402  # Energy Monitor
REC_DVL_STATUS    = 0x040b  # DVL Status
REC_SUBSYS_MODE   = 0x0408  # Subsystem Mode
REC_STARTUP_FLAG  = 0x0446  # Startup Flag
REC_EVENT_MARKER  = 0x03ef  # Event Marker

RECORD_NAMES = {
    REC_NAV:           'Navigation',
    REC_CTD_YSI:       'YSI CTD',
    REC_CTD_SBE:       'Seabird CTD (SBE49)',
    REC_ADCP:          'ADCP/DVL (1200 kHz)',
    REC_SIDESCAN:      'Sidescan (900 kHz)',
    REC_ECO:           'Wetlabs ECO BB2F',
    REC_GPS:           'GPS/Acoustic Nav',
    REC_VEHICLE_NAME:  'Vehicle Name',
    REC_VEHICLE_INFO:  'Vehicle Info',
    REC_MANUFACTURER:  'Manufacturer Info',
    REC_MODEM_LOG:     'Acoustic Modem Log',
    REC_DIAGNOSTIC:    'Diagnostic Log',
    REC_MISSION_MODES: 'Mission Modes',
    REC_MISSION_LEGS:  'Mission Legs',
    REC_SENSOR_NAMES:  'Sensor Names',
    REC_SENSOR_TYPES:  'Sensor Types',
    REC_SENSOR_DISPLAY: 'Sensor Display Config',
    REC_NAV_ACOUSTIC:  'Nav/Acoustic',
    REC_DATA_CHANNELS: 'Data Channels',
    REC_WAYPOINTS:     'Waypoints',
    REC_ECO_CAL:       'ECO Calibration',
    REC_ACOUSTIC_FIX:  'Acoustic Nav Fix',
    REC_BATTERY_STATUS: 'Battery Status',
    REC_BATTERY_CELLS:  'Battery Cell Data',
    REC_OBJ_NAV:       'Objective Navigation',
    REC_COMPASS_CAL:   'Compass Calibration',
    REC_HOUSING_TEMP:  'Housing Temperature',
    REC_ENERGY_MON:    'Energy Monitor',
    REC_DVL_STATUS:    'DVL Status',
    REC_SUBSYS_MODE:   'Subsystem Mode',
    REC_STARTUP_FLAG:  'Startup Flag',
    REC_EVENT_MARKER:  'Event Marker',
}

# Sentinel value used by MSTL sidescan for invalid data
SIDESCAN_SENTINEL = -32.768


# ---------------------------------------------------------------------------
# Low-level parsing
# ---------------------------------------------------------------------------

def parse_raw_records(data):
    """Parse raw binary data into a dict of {record_type: [payload_bytes, ...]}."""
    records = {}
    pos = 0
    end = len(data) - HEADER_SIZE
    while pos < end:
        if data[pos] == 0xEB and data[pos + 1] == 0x90:
            _cksum, rtype, plen = struct.unpack_from('<HHH', data, pos + 2)
            payload_end = pos + HEADER_SIZE + plen
            if payload_end <= len(data):
                payload = data[pos + HEADER_SIZE:payload_end]
                records.setdefault(rtype, []).append(payload)
                pos = payload_end
                continue
        pos += 1
    return records


def unwrap_timestamps(ts_raw):
    """Unwrap uint32 ms-since-midnight timestamps into hours from start.

    Handles the midnight UTC rollover (~86.4 M ms jump) and masks bit 31
    (flag bit).

    Parameters
    ----------
    ts_raw : np.ndarray, dtype uint32
        Raw timestamp values from records.

    Returns
    -------
    np.ndarray, dtype float64
        Time in hours from the first sample.
    """
    ts = ts_raw.astype(np.float64)
    ts = np.where(ts > 0x7FFFFFFF, ts - 0x80000000, ts)
    for i in range(1, len(ts)):
        if ts[i] - ts[i - 1] < -1_000_000:
            ts[i:] += 86_400_000
    return (ts - ts[0]) / 3_600_000.0


# ---------------------------------------------------------------------------
# Record decoders
# ---------------------------------------------------------------------------

def decode_nav(payloads):
    """Decode Navigation records (0x044e, 46 bytes).

    Fields
    ------
    off  0: float64 LE  Latitude (degrees N)
    off  8: float64 LE  Longitude (degrees E, negative = W)
    off 16: uint32 LE   Timestamp (ms since midnight UTC)
    off 20: float32 LE  Speed (m/s)
    off 24: uint16 LE   Altimeter max range config (constant 10, from Imagenex 852 settings)
    off 26: float32 LE  Pitch (degrees, confirmed via ADCP cross-validation)
    off 30: float32 LE  (constant 90.0 — altimeter config parameter)
    off 34: float32 LE  Vehicle depth below surface (m)
    off 38: float32 LE  (depth duplicate — byte-identical to off 34)
    off 42: float32 LE  Undecoded float (mean ~-7, range ±23; not pitch/roll/depth)
    """
    N = len(payloads)
    out = {
        'lat':    np.empty(N, dtype=np.float64),
        'lon':    np.empty(N, dtype=np.float64),
        'ts_raw': np.empty(N, dtype=np.uint32),
        'speed':  np.empty(N, dtype=np.float32),
        'alt_max_range': np.empty(N, dtype=np.uint16),
        'pitch':  np.empty(N, dtype=np.float32),
        'depth':  np.empty(N, dtype=np.float32),
        'undecoded_f42': np.empty(N, dtype=np.float32),
    }
    for i, p in enumerate(payloads):
        out['lat'][i]    = struct.unpack_from('<d', p, 0)[0]
        out['lon'][i]    = struct.unpack_from('<d', p, 8)[0]
        out['ts_raw'][i] = struct.unpack_from('<I', p, 16)[0]
        out['speed'][i]  = struct.unpack_from('<f', p, 20)[0]
        out['alt_max_range'][i] = struct.unpack_from('<H', p, 24)[0]
        out['pitch'][i]  = struct.unpack_from('<f', p, 26)[0]
        out['depth'][i]  = struct.unpack_from('<f', p, 34)[0]
        out['undecoded_f42'][i] = struct.unpack_from('<f', p, 42)[0]
    out['t_hrs'] = unwrap_timestamps(out['ts_raw'])
    return out


def decode_ctd_ysi(payloads):
    """Decode YSI CTD records (0x041d, 40 bytes).

    Fields
    ------
    off  0: float64 LE  Latitude (degrees)
    off  8: float64 LE  Longitude (degrees)
    off 16: uint32 LE   Timestamp (ms since midnight UTC)
    off 20: float32 LE  Unknown float (range 0.02-42; median ~4 when submerged)
    off 24: float32 LE  Conductivity (mS/cm)
    off 28: float32 LE  Temperature (deg C)
    off 32: float32 LE  Salinity (PSU)
    off 36: float32 LE  Speed of Sound (m/s)
    """
    N = len(payloads)
    out = {
        'lat':          np.empty(N, dtype=np.float64),
        'lon':          np.empty(N, dtype=np.float64),
        'ts_raw':       np.empty(N, dtype=np.uint32),
        'undecoded_f20': np.empty(N, dtype=np.float32),
        'conductivity': np.empty(N, dtype=np.float32),
        'temperature':  np.empty(N, dtype=np.float32),
        'salinity':     np.empty(N, dtype=np.float32),
        'sound_speed':  np.empty(N, dtype=np.float32),
    }
    for i, p in enumerate(payloads):
        out['lat'][i]           = struct.unpack_from('<d', p, 0)[0]
        out['lon'][i]           = struct.unpack_from('<d', p, 8)[0]
        out['ts_raw'][i]        = struct.unpack_from('<I', p, 16)[0]
        out['undecoded_f20'][i] = struct.unpack_from('<f', p, 20)[0]
        out['conductivity'][i]  = struct.unpack_from('<f', p, 24)[0]
        out['temperature'][i]   = struct.unpack_from('<f', p, 28)[0]
        out['salinity'][i]      = struct.unpack_from('<f', p, 32)[0]
        out['sound_speed'][i]   = struct.unpack_from('<f', p, 36)[0]
    out['t_hrs'] = unwrap_timestamps(out['ts_raw'])
    return out


def decode_ctd_sbe(payloads):
    """Decode Seabird CTD / SBE49 records (0x040a, 32 bytes).

    Fields
    ------
    off  0: float32 LE  Latitude (degrees, lower precision than double)
    off  4: float32 LE  Longitude (degrees)
    off  8: uint32 LE   Timestamp (ms since midnight UTC)
    off 12: float32 LE  Altitude (m above bottom, tentative)
    off 16: float32 LE  Conductivity (mS/cm)
    off 20: float32 LE  Temperature (deg C)
    off 24: float32 LE  Salinity (PSU)
    off 28: float32 LE  Speed of Sound (m/s)
    """
    N = len(payloads)
    out = {
        'lat':          np.empty(N, dtype=np.float32),
        'lon':          np.empty(N, dtype=np.float32),
        'ts_raw':       np.empty(N, dtype=np.uint32),
        'altitude':     np.empty(N, dtype=np.float32),
        'conductivity': np.empty(N, dtype=np.float32),
        'temperature':  np.empty(N, dtype=np.float32),
        'salinity':     np.empty(N, dtype=np.float32),
        'sound_speed':  np.empty(N, dtype=np.float32),
    }
    for i, p in enumerate(payloads):
        out['lat'][i]          = struct.unpack_from('<f', p, 0)[0]
        out['lon'][i]          = struct.unpack_from('<f', p, 4)[0]
        out['ts_raw'][i]       = struct.unpack_from('<I', p, 8)[0]
        out['altitude'][i]     = struct.unpack_from('<f', p, 12)[0]
        out['conductivity'][i] = struct.unpack_from('<f', p, 16)[0]
        out['temperature'][i]  = struct.unpack_from('<f', p, 20)[0]
        out['salinity'][i]     = struct.unpack_from('<f', p, 24)[0]
        out['sound_speed'][i]  = struct.unpack_from('<f', p, 28)[0]
    out['t_hrs'] = unwrap_timestamps(out['ts_raw'])
    return out


def decode_adcp(payloads):
    """Decode ADCP / DVL records (0x03e8, 155 bytes).

    The onboard RDI 1200 kHz DVL has upward- and downward-looking transducers,
    sampling water velocities in 1 m bins at ~0.67 Hz.

    Fields
    ------
    off  0: uint8       Sub-type / flag (constant 0x15 = 21)
    off  1: float32 LE  ADCP internal parameter (~38, possibly battery voltage)
    off  5: float32 LE  Attitude value (degrees, small)
    off  9: float32 LE  ADCP parameter (~755, partially decoded)
    off 13: float32 LE  Depth-related value (~3 m)
    off 17: float32 LE  Depth-related value (~3 m)
    off 21: float32 LE  Config / scale constant (= 100)
    off 25: float32 LE  Water temperature (deg C, from ADCP sensor)
    off 29: float32 LE  Altitude above bottom (m)
    off 33: float32 LE  Vehicle depth below surface (m)
    off 37: float32 LE  Pitch (degrees)
    off 41: float32 LE  Roll (degrees)
    off 45: float32 LE  Attitude angle (degrees, partially decoded)
    off 53: float32 LE  Heading (degrees, 0-360)
    off 57: float32 LE  Bearing (degrees, 0-360)
    off 67: float64 LE  Position 1 — Latitude  (degrees)
    off 75: float64 LE  Position 1 — Longitude (degrees)
    off 83: float64 LE  Position 2 — Latitude  (degrees)
    off 91: float64 LE  Position 2 — Longitude (degrees)
    off 99: float64 LE  Position 3 — Latitude  (degrees)
    off107: float64 LE  Position 3 — Longitude (degrees)
    off115-154: Tail data (partially decoded, includes status bytes)
    """
    N = len(payloads)
    out = {
        'subtype':    np.empty(N, dtype=np.uint8),
        'adcp_param1': np.empty(N, dtype=np.float32),
        'attitude1':  np.empty(N, dtype=np.float32),
        'adcp_param2': np.empty(N, dtype=np.float32),
        'depth1':     np.empty(N, dtype=np.float32),
        'depth2':     np.empty(N, dtype=np.float32),
        'config_val': np.empty(N, dtype=np.float32),
        'water_temp': np.empty(N, dtype=np.float32),
        'altitude':   np.empty(N, dtype=np.float32),
        'depth':      np.empty(N, dtype=np.float32),
        'pitch':      np.empty(N, dtype=np.float32),
        'roll':       np.empty(N, dtype=np.float32),
        'attitude2':  np.empty(N, dtype=np.float32),
        'heading':    np.empty(N, dtype=np.float32),
        'bearing':    np.empty(N, dtype=np.float32),
        'lat1':       np.empty(N, dtype=np.float64),
        'lon1':       np.empty(N, dtype=np.float64),
        'lat2':       np.empty(N, dtype=np.float64),
        'lon2':       np.empty(N, dtype=np.float64),
        'lat3':       np.empty(N, dtype=np.float64),
        'lon3':       np.empty(N, dtype=np.float64),
    }
    for i, p in enumerate(payloads):
        out['subtype'][i]     = p[0]
        out['adcp_param1'][i] = struct.unpack_from('<f', p, 1)[0]
        out['attitude1'][i]   = struct.unpack_from('<f', p, 5)[0]
        out['adcp_param2'][i] = struct.unpack_from('<f', p, 9)[0]
        out['depth1'][i]      = struct.unpack_from('<f', p, 13)[0]
        out['depth2'][i]      = struct.unpack_from('<f', p, 17)[0]
        out['config_val'][i]  = struct.unpack_from('<f', p, 21)[0]
        out['water_temp'][i]  = struct.unpack_from('<f', p, 25)[0]
        out['altitude'][i]    = struct.unpack_from('<f', p, 29)[0]
        out['depth'][i]       = struct.unpack_from('<f', p, 33)[0]
        out['pitch'][i]       = struct.unpack_from('<f', p, 37)[0]
        out['roll'][i]        = struct.unpack_from('<f', p, 41)[0]
        out['attitude2'][i]   = struct.unpack_from('<f', p, 45)[0]
        out['heading'][i]     = struct.unpack_from('<f', p, 53)[0]
        out['bearing'][i]     = struct.unpack_from('<f', p, 57)[0]
        out['lat1'][i]        = struct.unpack_from('<d', p, 67)[0]
        out['lon1'][i]        = struct.unpack_from('<d', p, 75)[0]
        out['lat2'][i]        = struct.unpack_from('<d', p, 83)[0]
        out['lon2'][i]        = struct.unpack_from('<d', p, 91)[0]
        out['lat3'][i]        = struct.unpack_from('<d', p, 99)[0]
        out['lon3'][i]        = struct.unpack_from('<d', p, 107)[0]
    return out


def decode_sidescan(payloads):
    """Decode MSTL Sidescan records (0x03f7, 55 bytes, 900 kHz).

    Fields
    ------
    off  0: float32 LE  Latitude (degrees, lower precision)
    off  4: float32 LE  Longitude (degrees)
    off  8: float32 LE  Altitude above bottom (m)
    off 12: float32 LE  Vehicle depth below surface (m)
    off 32: float32 LE  Temperature-like value (~28 deg C)
    off 38: float32 LE  Heading (degrees, 0-360)

    Sentinel value -32.768 marks invalid data.
    """
    N = len(payloads)
    out = {
        'lat':         np.empty(N, dtype=np.float32),
        'lon':         np.empty(N, dtype=np.float32),
        'altitude':    np.empty(N, dtype=np.float32),
        'depth':       np.empty(N, dtype=np.float32),
        'temperature': np.empty(N, dtype=np.float32),
        'heading':     np.empty(N, dtype=np.float32),
    }
    for i, p in enumerate(payloads):
        out['lat'][i]         = struct.unpack_from('<f', p, 0)[0]
        out['lon'][i]         = struct.unpack_from('<f', p, 4)[0]
        out['altitude'][i]    = struct.unpack_from('<f', p, 8)[0]
        out['depth'][i]       = struct.unpack_from('<f', p, 12)[0]
        out['temperature'][i] = struct.unpack_from('<f', p, 32)[0]
        out['heading'][i]     = struct.unpack_from('<f', p, 38)[0]
    # Replace sentinel values with NaN
    for key in ('altitude', 'depth', 'temperature'):
        out[key] = np.where(np.abs(out[key] - SIDESCAN_SENTINEL) < 0.01,
                            np.nan, out[key])
    return out


def decode_eco(payloads):
    """Decode Wetlabs ECO BB2F records (0x043e, 57 bytes).

    The ECO BB2F measures optical backscatter at 470 nm and 650 nm plus
    chlorophyll-a fluorescence.  Channel mapping from 130906.ini config:

        Ref470       raw counts        (position 1)
        Lambda470    raw counts        (position 2)
        Beta470      1/m/sr, LINEAR    (scale 2.4E-5, offset 50, position 2)
        Ref650       raw counts        (position 3)
        Lambda650    raw counts        (position 4)
        Beta650      1/m/sr, LINEAR    (scale 3.26E-06, offset 100, position 4)
        Chlorophyll  ug/L, LINEAR      (scale 0.016, offset 75, position 5)
        Thermistor   raw counts        (position 6)

    Fields
    ------
    off  0: float64 LE  Latitude (degrees)
    off  8: float64 LE  Longitude (degrees)
    off 16: uint32 LE   Timestamp (ms since midnight UTC)
    off 20: float32 LE  Vehicle depth (m)
    off 25: float32 LE  Ref470 (raw counts, ~1000)
    off 29: float32 LE  Lambda470 / signal counts (~93)
    off 33: float32 LE  Beta470 derived (1/m/sr, ~0.001)
    off 37: float32 LE  Ref650 (raw counts, ~719)
    off 41: float32 LE  Lambda650 / signal counts (~160)
    off 45: float32 LE  Beta650 derived (1/m/sr, ~0.0002)
    off 49: float32 LE  Chlorophyll proxy (derived, ~-0.1)
    off 53: float32 LE  Thermistor (raw counts, ~526)

    Note: ECO channels are at 1-byte-offset alignment (fields start at
    byte 25, not byte 24), suggesting a single padding/flag byte at offset 24.
    """
    N = len(payloads)
    out = {
        'lat':         np.empty(N, dtype=np.float64),
        'lon':         np.empty(N, dtype=np.float64),
        'ts_raw':      np.empty(N, dtype=np.uint32),
        'depth':       np.empty(N, dtype=np.float32),
        'ref470':      np.empty(N, dtype=np.float32),
        'lambda470':   np.empty(N, dtype=np.float32),
        'beta470':     np.empty(N, dtype=np.float32),
        'ref650':      np.empty(N, dtype=np.float32),
        'lambda650':   np.empty(N, dtype=np.float32),
        'beta650':     np.empty(N, dtype=np.float32),
        'chlorophyll': np.empty(N, dtype=np.float32),
        'thermistor':  np.empty(N, dtype=np.float32),
    }
    for i, p in enumerate(payloads):
        out['lat'][i]         = struct.unpack_from('<d', p, 0)[0]
        out['lon'][i]         = struct.unpack_from('<d', p, 8)[0]
        out['ts_raw'][i]      = struct.unpack_from('<I', p, 16)[0]
        out['depth'][i]       = struct.unpack_from('<f', p, 20)[0]
        out['ref470'][i]      = struct.unpack_from('<f', p, 25)[0]
        out['lambda470'][i]   = struct.unpack_from('<f', p, 29)[0]
        out['beta470'][i]     = struct.unpack_from('<f', p, 33)[0]
        out['ref650'][i]      = struct.unpack_from('<f', p, 37)[0]
        out['lambda650'][i]   = struct.unpack_from('<f', p, 41)[0]
        out['beta650'][i]    = struct.unpack_from('<f', p, 45)[0]
        out['chlorophyll'][i] = struct.unpack_from('<f', p, 49)[0]
        out['thermistor'][i]  = struct.unpack_from('<f', p, 53)[0]
    out['t_hrs'] = unwrap_timestamps(out['ts_raw'])
    return out


def decode_gps(payloads):
    """Decode GPS / Acoustic Nav records (0x03f9, 59 bytes).

    Fields
    ------
    off  0: float64 LE  Latitude (degrees)
    off  8: float64 LE  Longitude (degrees)
    off 16: uint16 LE   GPS-related field
    off 18-30: Various navigation fields (partially decoded)
    off 31+: May contain ASCII transponder IDs (e.g. "REMUS214", "REMUS275")
    """
    N = len(payloads)
    out = {
        'lat':    np.empty(N, dtype=np.float64),
        'lon':    np.empty(N, dtype=np.float64),
    }
    ascii_strings = []
    for i, p in enumerate(payloads):
        out['lat'][i] = struct.unpack_from('<d', p, 0)[0]
        out['lon'][i] = struct.unpack_from('<d', p, 8)[0]
        # Extract any ASCII content from bytes 31 onwards
        text = bytes(b for b in p[31:] if 0x20 <= b < 0x7F).decode('ascii', errors='ignore')
        if text:
            ascii_strings.append(text)
    out['ascii_content'] = ascii_strings
    return out


def decode_vehicle_name(payloads):
    """Decode Vehicle Name records (0x03f4, 35 bytes).

    Fields
    ------
    off  0: uint8   Sub-type flag (0x15)
    off  1: str     Null-terminated vehicle name (e.g. 'Aukai')
    """
    name = payloads[0][1:].split(b'\x00')[0].decode('ascii', errors='replace')
    return {'name': name}


def decode_vehicle_info(payloads):
    """Decode Vehicle Info / startup log records (0x040d, variable length).

    Each record contains a label/value pair separated by '\\n', logged at
    vehicle startup.  Returns a dict mapping label to value string.

    Example entries
    ---------------
    'Vehicle Serial Number' -> 'SN 256'
    'Vehicle Owner and ID'  -> 'UH Aukai'
    'RDI ADCP'              -> 'Navigator Broadband DVL Version 19.13'
    'Seabird SBE-49 CTD'    -> 'FastCAT V 1.2a  SERIAL NO. 0122'
    'Smart battery 0'       -> 'RE003 (Cell #31121) PIC Bd#2723, Dec  2 2009 18:02:07'
    """
    info = {}
    for p in payloads:
        text = p[2:].split(b'\x00')[0].decode('ascii', errors='replace').strip()
        if '\n' in text:
            label, value = text.split('\n', 1)
            info[label.strip()] = value.strip()
        elif text:
            info[text] = ''
    return info


def decode_manufacturer_info(payloads):
    """Decode Manufacturer Info records (0x0416, 108 bytes).

    Fields
    ------
    off  0: uint8   Flag (0x00)
    off  1: str     Null-terminated manufacturer string

    Example
    -------
    'Manufactured by Hydroid, Inc. 6 Benjamin Nye Circle, Pocasset, Ma. 02559
     (508)-563-6565 www.hydroidinc.com'
    """
    info = payloads[0][1:].split(b'\x00')[0].decode('ascii', errors='replace')
    return {'info': info}


def decode_diagnostic(payloads):
    """Decode Firmware Diagnostic / Warning Log records (0x03e9, variable length).

    Each record is emitted by the REMUS firmware when a diagnostic condition
    is detected.  The payload structure is:

        {source_file}\\x00  — null-terminated C++ source filename
        2 bytes             — uint16 LE (firmware-internal code)
        4 bytes             — constant marker (0x47 0x3b 0x2a 0x52 = 'G;*R')
        {message}\\x00     — null-terminated warning/diagnostic text
        1 byte              — trailing flag

    Source filenames observed: DIAGNOSE.CPP, SCAN_VEH.CPP, OBJECTIV.CPP

    Returns
    -------
    list of dicts, each with:
        'source_file' : str  C++ source file that emitted the warning
        'message'     : str  Human-readable diagnostic message
    """
    records = []
    for p in payloads:
        # Extract null-terminated source filename
        null = p.find(b'\x00')
        if null < 0:
            records.append({'source_file': '', 'message': p.decode('ascii', errors='replace')})
            continue
        source_file = p[:null].decode('ascii', errors='replace')
        # Skip 6-byte separator (2 unknown + 4-byte 'G;*R' marker)
        rest = p[null + 1 + 6:]
        message = rest.split(b'\x00')[0].decode('ascii', errors='replace').strip()
        records.append({'source_file': source_file, 'message': message})
    return records


def decode_modem_log(payloads):
    """Decode Acoustic Modem Log records (0x0424, 36 bytes).

    Each record logs one acoustic modem message.  The payload contains a
    null-terminated string of the form:

        {dir}({source}) {counter}:{message}

    where dir is '>' (outgoing from vehicle) or '<' (incoming to vehicle).

    Fields
    ------
    off  0: uint8   Flag (0x01)
    off  1: uint8   Padding (0x00)
    off  2: str     Null-terminated message string

    Returns
    -------
    dict with lists:
        'direction' : list of str  '>' or '<'
        'source'    : list of str  e.g. 'VehM', 'Veh'
        'counter'   : list of int  per-record sequence number
        'message'   : list of str  message body
    """
    import re
    _pat = re.compile(r'^([><])\((\w+)\)\s+(\d+):(.*)')
    directions, sources, counters, messages = [], [], [], []
    for p in payloads:
        text = p[2:].split(b'\x00')[0].decode('ascii', errors='replace').strip()
        m = _pat.match(text)
        if m:
            directions.append(m.group(1))
            sources.append(m.group(2))
            counters.append(int(m.group(3)))
            messages.append(m.group(4).strip())
        else:
            directions.append('')
            sources.append('')
            counters.append(-1)
            messages.append(text)
    return {
        'direction': directions,
        'source':    sources,
        'counter':   counters,
        'message':   messages,
    }


def decode_mission_modes(payloads):
    """Decode Mission Mode type lookup table (0x03ee, 21 bytes).

    A static table of all mission/objective mode types supported by the
    REMUS firmware, logged at startup.  Returns a dict mapping integer
    mode index to mode name string.

    Example entries
    ---------------
    0  -> 'Manual'
    6  -> 'Wait run'
    13 -> 'Compass cal'
    14 -> 'Navigate'
    """
    modes = {}
    for p in payloads:
        idx  = p[0]
        name = p[4:].split(b'\x00')[0].decode('ascii', errors='replace')
        modes[idx] = name
    return modes


def decode_mission_legs(payloads):
    """Decode Mission Leg / Objective Waypoint records (0x03f0, 48 bytes).

    Each record defines one leg (objective) in the mission plan, including
    its geographic position, objective type name, and destination name.

    Fields
    ------
    off  0: uint8    Leg type flag
    off  2-9:  float64 LE  Latitude  (degrees N)
    off 10-17: float64 LE  Longitude (degrees E)
    off 24-33: str         Objective type name (null-padded, 10 bytes)
                           e.g. 'ADVStack', 'SADCP', 'NWCRNR2', 'Waypoint'
    off 34-43: str         Destination name (null-padded, 10 bytes)
    off 46-47: uint16 LE   Leg index
    """
    N = len(payloads)
    out = {
        'leg_type': np.empty(N, dtype=np.uint8),
        'lat':      np.empty(N, dtype=np.float64),
        'lon':      np.empty(N, dtype=np.float64),
        'index':    np.empty(N, dtype=np.uint16),
        'type_name': [],
        'dest_name': [],
    }
    for i, p in enumerate(payloads):
        out['leg_type'][i] = p[0]
        out['lat'][i]      = struct.unpack_from('<d', p, 2)[0]
        out['lon'][i]      = struct.unpack_from('<d', p, 10)[0]
        out['index'][i]    = struct.unpack_from('<H', p, 46)[0]
        out['type_name'].append(p[24:34].split(b'\x00')[0].decode('ascii', errors='replace'))
        out['dest_name'].append(p[34:44].split(b'\x00')[0].decode('ascii', errors='replace'))
    return out


def decode_sensor_names(payloads):
    """Decode Sensor Name string records (0x03fc, 13 bytes).

    Each record holds one sensor name in an 11-byte null-padded field.
    Returns a list of unique sensor names in order of first appearance.

    Example: ['RDI ADCP', 'Imagenex852', 'YSI CTD', 'Seabird', ...]
    """
    seen = []
    for p in payloads:
        name = p[:11].split(b'\x00')[0].decode('ascii', errors='replace')
        if name and name not in seen:
            seen.append(name)
    return seen


def decode_sensor_types(payloads):
    """Decode Sensor Type ID-to-name mapping records (0x0407, 23 bytes).

    A static lookup table mapping firmware sensor type codes to human-readable
    names. Returns a dict of {type_code (int): name (str)}.

    Fields
    ------
    off  0: uint8   Sensor type code
    off  1-11: str  Null-padded sensor name (11 bytes)

    Example entries
    ---------------
    0x15 -> 'Gyro Chip'
    0x16 -> 'ADCP'
    0x17 -> 'Bottom Lock'
    0x18 -> 'ImagenexAlt'
    0x19 -> 'Temp.'
    0x1a -> 'Housing'
    """
    types = {}
    for p in payloads:
        code = p[0]
        name = p[1:12].split(b'\x00')[0].decode('ascii', errors='replace')
        types[code] = name
    return types


def decode_sensor_display(payloads):
    """Decode Sensor Display Format configuration records (0x040c, 28 bytes).

    Defines the display name, value range, and printf format string for a
    sensor channel as configured in the vehicle GUI/logging software.

    Fields
    ------
    off  0: uint8    Type flag
    off  1: uint8    Sub-type flag
    off  2-5:  float32 LE  Minimum display value
    off  6-9:  float32 LE  Maximum display value
    off 10-19: str         Sensor name (null-padded, 10 bytes)
    off 21+:   str         Printf format string (null-terminated)
    """
    configs = []
    for p in payloads:
        min_val = struct.unpack_from('<f', p, 2)[0]
        max_val = struct.unpack_from('<f', p, 6)[0]
        name    = p[10:20].split(b'\x00')[0].decode('ascii', errors='replace')
        fmt     = p[21:].split(b'\x00')[0].decode('ascii', errors='replace')
        configs.append({'name': name, 'min': min_val, 'max': max_val, 'format': fmt})
    return configs


def decode_nav_acoustic(payloads):
    """Decode Navigation / Acoustic Positioning records (0x041a, 57 bytes).

    Logged at the acoustic navigation update rate (~2144 records per full
    mission day).  Records with invalid fixes carry -1.0 sentinels in the
    float fields and zeros in the position fields.

    Fields
    ------
    off  8-11:  float32 LE  Heading — DVL/ADCP internal heading (degrees, -1=invalid)
    off 12-15:  float32 LE  Sound speed — DVL-reported (m/s, -1=invalid)
    off 24-31:  float64 LE  Latitude  (degrees N, 0 when invalid)
    off 32-39:  float64 LE  Longitude (degrees E, 0 when invalid)
    off 40-43:  float32 LE  Heading — vehicle compass (degrees)
    off 44-47:  float32 LE  Sound speed — CTD-derived (m/s)
    """
    N = len(payloads)
    BAD = -1.0
    out = {
        'heading_dvl':   np.empty(N, dtype=np.float32),
        'sound_speed_dvl': np.empty(N, dtype=np.float32),
        'lat':           np.empty(N, dtype=np.float64),
        'lon':           np.empty(N, dtype=np.float64),
        'heading':       np.empty(N, dtype=np.float32),
        'sound_speed':   np.empty(N, dtype=np.float32),
    }
    for i, p in enumerate(payloads):
        out['heading_dvl'][i]    = struct.unpack_from('<f', p, 8)[0]
        out['sound_speed_dvl'][i] = struct.unpack_from('<f', p, 12)[0]
        lat = struct.unpack_from('<d', p, 24)[0]
        lon = struct.unpack_from('<d', p, 32)[0]
        out['lat'][i] = lat if (15 < abs(lat) < 90) else np.nan
        out['lon'][i] = lon if (90 < abs(lon) < 180) else np.nan
        out['heading'][i]      = struct.unpack_from('<f', p, 40)[0]
        out['sound_speed'][i]  = struct.unpack_from('<f', p, 44)[0]
    # Replace -1 sentinels with NaN
    for key in ('heading_dvl', 'sound_speed_dvl'):
        out[key] = np.where(out[key] == BAD, np.nan, out[key])
    return out


def decode_data_channels(payloads):
    """Decode Internal Data Type Channel definition records (0x041c, 24 bytes).

    A static lookup table of internal firmware data channel IDs and their
    associated sampling rate.  Returns a list of unique channel dicts.

    Fields
    ------
    off  0-1:  uint16 LE  Channel index
    off  2-11: str        Channel name (null-padded, 10 bytes), e.g. 'DT1A'
    off 22-23: uint16 LE  Nominal sample period (ms)
    """
    channels = []
    seen = set()
    for p in payloads:
        idx     = struct.unpack_from('<H', p, 0)[0]
        name    = p[2:12].split(b'\x00')[0].decode('ascii', errors='replace')
        rate_ms = struct.unpack_from('<H', p, 22)[0]
        if (idx, name) not in seen:
            seen.add((idx, name))
            channels.append({'index': idx, 'name': name, 'rate_ms': rate_ms})
    return channels


def decode_waypoints(payloads):
    """Decode Mission Waypoint records (0x0427, 31-32 bytes).

    Each record defines one named waypoint used during the mission
    (e.g. compass calibration site, leg turn points).

    Fields
    ------
    off  0-7:  float64 LE  Latitude  (degrees N)
    off  8-15: float64 LE  Longitude (degrees E)
    off 16-17: uint16 LE   Waypoint flags
    off 18+:   str         Null-terminated waypoint name
    """
    waypoints = []
    for p in payloads:
        lat   = struct.unpack_from('<d', p, 0)[0]
        lon   = struct.unpack_from('<d', p, 8)[0]
        flags = struct.unpack_from('<H', p, 16)[0]
        name  = p[18:].split(b'\x00')[0].decode('ascii', errors='replace')
        waypoints.append({'lat': lat, 'lon': lon, 'flags': flags, 'name': name})
    return waypoints


def decode_eco_calibration(payloads):
    """Decode ECO BB2F sensor channel calibration records (0x043d, 46 bytes).

    One record per ECO channel, repeated each mission.  Contains the channel
    name, physical units, and linear calibration coefficients
    (physical = scale * (raw_counts - offset)).

    Fields
    ------
    off  0-16: str        Channel name, null-padded (17 bytes)
                          e.g. 'Ref470', 'Beta470', 'Chlorophyll A'
    off 17-33: str        Physical units, null-padded (17 bytes)
                          e.g. 'Counts', 'B/m/sterad', 'ug/liter'
    off 34:    uint8      Channel index (0-based position in ECO output)
    off 35:    uint8      Calibrated flag (0=raw counts, 1+=has calibration)
    off 38-41: float32 LE Scale factor
    off 42-45: float32 LE Offset (subtracted from raw before scaling)
    """
    channels = []
    for p in payloads:
        channel    = p[0:17].split(b'\x00')[0].decode('ascii', errors='replace')
        units      = p[17:34].split(b'\x00')[0].decode('ascii', errors='replace')
        index      = p[34]
        calibrated = bool(p[35])
        scale      = struct.unpack_from('<f', p, 38)[0]
        offset     = struct.unpack_from('<f', p, 42)[0]
        channels.append({
            'channel':    channel,
            'units':      units,
            'index':      index,
            'calibrated': calibrated,
            'scale':      scale,
            'offset':     offset,
        })
    return channels


def decode_acoustic_fix(payloads):
    """Decode Acoustic Transponder Navigation Fix records (0x041f, 126 bytes).

    Logged each time the vehicle computes a transponder-based position fix
    (~114 records per mission day).  Each record carries a full UTC wall-clock
    timestamp (year/month/day/hour/minute/second) and the acoustic range to the
    active transponder.

    Fields
    ------
    off  0-7:  float64 LE  Latitude  (degrees N)
    off  8-15: float64 LE  Longitude (degrees E)
    off 16-19: float32 LE  Vehicle heading (degrees, 0-360)
    off 20-21: uint16 LE   Sequence counter
    off 22-23: uint16 LE   Number of active transponders
    off 26-29: float32 LE  Vehicle speed at fix time (m/s)
    off 30-33: float32 LE  Acoustic slant range to transponder (m)
    off 46:    uint8       Year (2-digit, add 2000)
    off 47:    uint8       Month
    off 48:    uint8       Day
    off 49:    uint8       Hour (UTC)
    off 50:    uint8       Minute
    off 51:    uint8       Second
    """
    N = len(payloads)
    out = {
        'lat':        np.empty(N, dtype=np.float64),
        'lon':        np.empty(N, dtype=np.float64),
        'heading':    np.empty(N, dtype=np.float32),
        'speed':      np.empty(N, dtype=np.float32),
        'range_m':    np.empty(N, dtype=np.float32),
        'seq':        np.empty(N, dtype=np.uint16),
        'n_transp':   np.empty(N, dtype=np.uint16),
        'datetime':   [],
    }
    for i, p in enumerate(payloads):
        out['lat'][i]      = struct.unpack_from('<d', p, 0)[0]
        out['lon'][i]      = struct.unpack_from('<d', p, 8)[0]
        out['heading'][i]  = struct.unpack_from('<f', p, 16)[0]
        out['seq'][i]      = struct.unpack_from('<H', p, 20)[0]
        out['n_transp'][i] = struct.unpack_from('<H', p, 22)[0]
        out['speed'][i]    = struct.unpack_from('<f', p, 26)[0]
        out['range_m'][i]  = struct.unpack_from('<f', p, 30)[0]
        yr, mo, dy, hr, mn, sc = p[46], p[47], p[48], p[49], p[50], p[51]
        out['datetime'].append(f'20{yr:02d}-{mo:02d}-{dy:02d} {hr:02d}:{mn:02d}:{sc:02d}')
    return out


def decode_battery_status(payloads):
    """Decode Smart Battery Status records (0x0412, 139 bytes).

    One record per battery per logging cycle, cycling through all four
    battery banks.  Contains the battery identity strings and real-time
    voltage measurements from the smart battery BMS.

    Constant fields (battery identity, logged once at startup)
    ----------------------------------------------------------
    Strings at tail:  part_number (e.g. 'RE003'), serial (e.g. '102455'),
                      chemistry ('LiION'), mfg_date ('Dec  2 2009'),
                      mfg_time ('18:02:07')
    off  8-9: uint16 LE  Rated capacity (mAh)          [constant: 5500]
    off 10-11: uint16 LE Design pack voltage (mV)       [constant: 28700]

    Time-varying fields
    -------------------
    off  2-3: uint16 LE  Battery index / ID code (cycles: 2722/2723/2724/2899)
    off 36-37: uint16 LE Cell voltage (mV, ~3047-3110 for LiION)
    off 38-39: uint16 LE Pack voltage (mV, ~25700-27740)
    """
    records = []
    for p in payloads:
        # Extract identity strings from null-separated tail
        parts = [pt.decode('ascii', errors='replace')
                 for pt in p.split(b'\x00')
                 if pt and all(0x20 <= b < 0x7f for b in pt) and len(pt) > 2]
        batt_id   = struct.unpack_from('<H', p, 2)[0]
        capacity  = struct.unpack_from('<H', p, 8)[0]
        design_mv = struct.unpack_from('<H', p, 10)[0]
        cell_mv   = struct.unpack_from('<H', p, 36)[0]
        pack_mv   = struct.unpack_from('<H', p, 38)[0]
        # Parse identity strings by content
        info = {}
        for s in parts:
            if s.startswith('RE'):
                info['part_number'] = s
            elif s.isdigit() and len(s) == 6:
                info['serial'] = s
            elif 'ION' in s or 'ACID' in s or 'NiMH' in s:
                info['chemistry'] = s
            elif any(m in s for m in ['Jan','Feb','Mar','Apr','May','Jun',
                                       'Jul','Aug','Sep','Oct','Nov','Dec']):
                info['mfg_date'] = s
            elif ':' in s and len(s) == 8:
                info['mfg_time'] = s
        records.append({
            'batt_id':    batt_id,
            'capacity_mAh': capacity,
            'design_mv':  design_mv,
            'cell_mv':    cell_mv,
            'pack_mv':    pack_mv,
            **info,
        })
    return records


def decode_battery_cells(payloads):
    """Decode Smart Battery Cell Data records (0x0413, 52 bytes).

    Companion record to Battery Status (0x0412), logged at the same rate.
    Contains real-time cell-level measurements: individual cell voltages and
    pack-level voltage.  Records cycle through the four battery banks in
    the same order as 0x0412.

    Fields
    ------
    off  6-7: uint16 LE  Nominal pack voltage (mV)   [constant: 25000]
    off 10-11: uint16 LE Cell voltage (mV, ~3089-3112)
    off 12-13: uint16 LE  Cumulative energy (mAh equivalent, varies)
    off 14-15: uint16 LE  Per-cycle energy (mAh, varies)
    off 16-17: uint16 LE  Rated capacity (mAh)        [constant: 5500]
    off 18-19: uint16 LE  Battery index / ID code (same cycling as 0x0412)
    off 38-51: uint16[7]  Individual cell voltages (raw counts, ~38700-39200)
    """
    records = []
    for p in payloads:
        batt_id    = struct.unpack_from('<H', p, 18)[0]
        cell_mv    = struct.unpack_from('<H', p, 10)[0]
        energy_cum = struct.unpack_from('<H', p, 12)[0]
        energy_cyc = struct.unpack_from('<H', p, 14)[0]
        capacity   = struct.unpack_from('<H', p, 16)[0]
        # 7 cell voltage readings at bytes 38-51
        cells = [struct.unpack_from('<H', p, 38 + k*2)[0] for k in range(7)]
        records.append({
            'batt_id':      batt_id,
            'cell_mv':      cell_mv,
            'energy_cum':   energy_cum,
            'energy_cyc':   energy_cyc,
            'capacity_mAh': capacity,
            'cell_counts':  cells,
        })
    return records


def decode_objective_nav(payloads):
    """Decode Objective Navigation records (0x03f1, 53 bytes).

    Real-time mission leg progress.  Each record tracks from-waypoint to
    to-waypoint navigation for the current objective.

    Fields (verified by binary analysis)
    ------
    off  0: uint8       Leg index (0-45, sequential then cycling)
    off  2: uint16 LE   Transit time estimate (seconds)
    off  4: uint16 LE   Leg distance estimate (meters, approximate — 5-15%
                         larger than haversine distance between FROM/TO)
    off  6: float64 LE  FROM latitude (degrees N)
    off 14: float64 LE  FROM longitude (degrees E)
    off 22: float64 LE  TO latitude (degrees N)
    off 30: float64 LE  TO longitude (degrees E)
    off 38: float32 LE  Commanded RPM (1736 or 1929)
    off 42: float32 LE  Commanded speed (m/s, typically 0 or 4)
    off 46: uint8       Mission mode index (cross-refs Mission Modes 0x03ee)
    off 48: uint8       Objective sub-type
    off 50: uint16 LE   Depth setpoint (dm, e.g. 40 = 4.0 m)
    off 52: uint8       Active flag (0=startup, 1=executing)

    Verification: FROM/TO positions are valid Makua Beach lat/lon; mode
    indices {11,13,14,15} map to 'Surface','Compass cal','Navigate',
    'Navigate rows' in the Mission Modes table; commanded RPM and speed
    are physically reasonable for REMUS-100.
    """
    N = len(payloads)
    out = {
        'leg_index':      np.empty(N, dtype=np.uint8),
        'transit_time_s': np.empty(N, dtype=np.uint16),
        'leg_dist_m':     np.empty(N, dtype=np.uint16),
        'from_lat':       np.empty(N, dtype=np.float64),
        'from_lon':       np.empty(N, dtype=np.float64),
        'to_lat':         np.empty(N, dtype=np.float64),
        'to_lon':         np.empty(N, dtype=np.float64),
        'cmd_rpm':        np.empty(N, dtype=np.float32),
        'cmd_speed':      np.empty(N, dtype=np.float32),
        'mode_index':     np.empty(N, dtype=np.uint8),
        'obj_subtype':    np.empty(N, dtype=np.uint8),
        'depth_setpt_dm': np.empty(N, dtype=np.uint16),
        'active':         np.empty(N, dtype=np.uint8),
    }
    for i, p in enumerate(payloads):
        out['leg_index'][i]      = p[0]
        out['transit_time_s'][i] = struct.unpack_from('<H', p, 2)[0]
        out['leg_dist_m'][i]     = struct.unpack_from('<H', p, 4)[0]
        out['from_lat'][i]       = struct.unpack_from('<d', p, 6)[0]
        out['from_lon'][i]       = struct.unpack_from('<d', p, 14)[0]
        out['to_lat'][i]         = struct.unpack_from('<d', p, 22)[0]
        out['to_lon'][i]         = struct.unpack_from('<d', p, 30)[0]
        out['cmd_rpm'][i]        = struct.unpack_from('<f', p, 38)[0]
        out['cmd_speed'][i]      = struct.unpack_from('<f', p, 42)[0]
        out['mode_index'][i]     = p[46]
        out['obj_subtype'][i]    = p[48]
        out['depth_setpt_dm'][i] = struct.unpack_from('<H', p, 50)[0]
        out['active'][i]         = p[52]
    return out


def decode_compass_cal(payloads):
    """Decode Compass Calibration records (0x0415, 48 bytes).

    Compass bias measurement data logged during calibration.  Reference
    headings at offset 4 exactly match the .ini compass bias table entries
    (254.8, 95.0, 275.0, 185.0, 5.0, 74.8 on 130906).

    Fields (verified by binary analysis)
    ------
    off  2: uint16 LE   Measurement counter (1-N per heading, resets each)
    off  4: float32 LE  Reference heading (from .ini bias table, degrees)
    off  8: float32 LE  Sensor reading #1 (range 100-290; NOT heading — corr
                         0.87 with RPM-like field @32; possibly magnetometer)
    off 12: float32 LE  Sensor reading #2 (similar to #1)
    off 16: float32 LE  Measured heading (degrees, clusters near ref heading)
    off 20: float32 LE  Corrected heading (degrees, clusters near ref heading)
    off 24: float32 LE  Heading error #1 (matches .ini bias corrections ±0.5°)
    off 28: float32 LE  Heading error #2 (secondary error estimate)
    off 32: float32 LE  Sensor/motor metric (range 1000-3900, corr 0.93 with @36)
    off 36: float32 LE  Sensor/motor metric (range 88-326, corr 0.87 with @8)
    off 40: float32 LE  Depth (m, ~7-11 during calibration)
    off 44: float32 LE  Scale/valid flag (constant 1.0)

    Verification: Mean heading errors by reference heading agree with .ini
    bias corrections: 254.8°→-1.05 (ini:-1.0), 95.0°→-1.28 (ini:-1.1),
    275.0°→+0.38 (ini:+0.5), 185.0°→+0.07 (ini:+0.3), 5.0°→-0.66
    (ini:-0.7), 74.8°→-2.03 (ini:-1.9).
    """
    N = len(payloads)
    out = {
        'counter':        np.empty(N, dtype=np.uint16),
        'ref_heading':    np.empty(N, dtype=np.float32),
        'sensor1':        np.empty(N, dtype=np.float32),
        'sensor2':        np.empty(N, dtype=np.float32),
        'meas_heading':   np.empty(N, dtype=np.float32),
        'corr_heading':   np.empty(N, dtype=np.float32),
        'heading_err1':   np.empty(N, dtype=np.float32),
        'heading_err2':   np.empty(N, dtype=np.float32),
        'motor_metric1':  np.empty(N, dtype=np.float32),
        'motor_metric2':  np.empty(N, dtype=np.float32),
        'depth':          np.empty(N, dtype=np.float32),
        'valid_flag':     np.empty(N, dtype=np.float32),
    }
    for i, p in enumerate(payloads):
        out['counter'][i]       = struct.unpack_from('<H', p, 2)[0]
        out['ref_heading'][i]   = struct.unpack_from('<f', p, 4)[0]
        out['sensor1'][i]       = struct.unpack_from('<f', p, 8)[0]
        out['sensor2'][i]       = struct.unpack_from('<f', p, 12)[0]
        out['meas_heading'][i]  = struct.unpack_from('<f', p, 16)[0]
        out['corr_heading'][i]  = struct.unpack_from('<f', p, 20)[0]
        out['heading_err1'][i]  = struct.unpack_from('<f', p, 24)[0]
        out['heading_err2'][i]  = struct.unpack_from('<f', p, 28)[0]
        out['motor_metric1'][i] = struct.unpack_from('<f', p, 32)[0]
        out['motor_metric2'][i] = struct.unpack_from('<f', p, 36)[0]
        out['depth'][i]         = struct.unpack_from('<f', p, 40)[0]
        out['valid_flag'][i]    = struct.unpack_from('<f', p, 44)[0]
    return out


def decode_housing_temp(payloads):
    """Decode Housing Temperature records (0x040e, 48 bytes).

    Electronics housing temperature with a sliding FIFO window of compass
    error history.

    Fields (verified by binary analysis)
    ------
    off  0: float32 LE  Compass heading correction (signed, ±16°)
    off  4: float32 LE  Compass bias drift (negative, -0.4 to -5.7)
    off  8: float32 LE  Housing temperature (°C, ~30°C; stable across missions)
    off 12: float32 LE  Newest FIFO entry (compass error magnitude)
    off 16-44: float32[8]  FIFO history (right-shifting: @16[i]==@20[i+1],
                            verified 266/266 exact matches on 130906,
                            360/361 on 130907)

    Notes: New values enter at offset 12 and shift RIGHT through offsets
    16→20→24→...→44, with the oldest value falling off at 44.
    """
    N = len(payloads)
    out = {
        'heading_correction': np.empty(N, dtype=np.float32),
        'bias_drift':         np.empty(N, dtype=np.float32),
        'housing_temp':       np.empty(N, dtype=np.float32),
        'compass_err_fifo':   np.empty((N, 9), dtype=np.float32),
    }
    for i, p in enumerate(payloads):
        out['heading_correction'][i] = struct.unpack_from('<f', p, 0)[0]
        out['bias_drift'][i]         = struct.unpack_from('<f', p, 4)[0]
        out['housing_temp'][i]       = struct.unpack_from('<f', p, 8)[0]
        for j in range(9):
            out['compass_err_fifo'][i, j] = struct.unpack_from('<f', p, 12 + j * 4)[0]
    return out


def decode_energy_monitor(payloads):
    """Decode Energy Monitor records (0x0402, 13 bytes).

    Battery energy consumption tracking.

    Fields (verified by binary analysis)
    ------
    off  0: uint8       Constant 7 (cell count or battery bank count)
    off  1: float32 LE  Battery capacity (Wh; 1235.86 constant on 130906,
                         but cycles through 613/931/1236 on 130907 —
                         possibly per-bank capacity)
    off  5: float32 LE  Energy metric (Wh; monotonically increasing 276→1010
                         on 130906, but goes negative on 130907)
    off  9: float32 LE  Status metric (mostly 830.36 on 130906; varies 0-886
                         on 130907)

    Note: The capacity field likely represents total available capacity of
    N active battery packs at ~309 Wh each: 1235.86/4=309.0, 930.69/3=
    310.2, 613.14/2=306.6 (verified across 130906-130908).  When a pack
    depletes, capacity drops.  On 130906 all 4 packs stayed active
    (constant 1235.86); on 130907/130908 packs dropped out mid-mission.
    """
    N = len(payloads)
    out = {
        'cell_count':     np.empty(N, dtype=np.uint8),
        'capacity_wh':    np.empty(N, dtype=np.float32),
        'energy_wh':      np.empty(N, dtype=np.float32),
        'status_metric':  np.empty(N, dtype=np.float32),
    }
    for i, p in enumerate(payloads):
        out['cell_count'][i]    = p[0]
        out['capacity_wh'][i]   = struct.unpack_from('<f', p, 1)[0]
        out['energy_wh'][i]     = struct.unpack_from('<f', p, 5)[0]
        out['status_metric'][i] = struct.unpack_from('<f', p, 9)[0]
    return out


def decode_dvl_status(payloads):
    """Decode DVL Status records (0x040b, 60 bytes).

    ADCP/DVL subsystem internal status/diagnostics.  First 22 bytes are
    mostly zeros (byte 11 = constant 8).  Active region at bytes 23-50
    with structured status data.  Internal format not fully determined.
    """
    records = []
    for p in payloads:
        records.append({'raw_hex': p.hex()})
    return records


def decode_subsystem_mode(payloads):
    """Decode Subsystem Mode records (0x0408, 6 bytes).

    Sparse mode/status flag register.  Only two patterns observed across
    the entire 130906 mission:
        04 b0 80 82 05 00  (27 occurrences — active mode)
        04 a0 80 00 00 00  (10 occurrences — idle/startup mode)
    """
    records = []
    for p in payloads:
        records.append({'raw_hex': p.hex()})
    return records


def decode_startup_flag(payloads):
    """Decode Startup Flag records (0x0446, 4 bytes).

    Constant payload 01 00 00 00.  Logged exactly 10 times per mission
    (same count as Vehicle Name records).  Startup/initialization marker.
    """
    return {'count': len(payloads), 'value': 1}


def decode_event_marker(payloads):
    """Decode Event Marker records (0x03ef, 0 bytes).

    Empty payload (~9 records per mission).  Phase transition or heartbeat
    marker.
    """
    return {'count': len(payloads)}


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

# Decoder dispatch table
_DECODERS = {
    REC_NAV:           decode_nav,
    REC_CTD_YSI:       decode_ctd_ysi,
    REC_CTD_SBE:       decode_ctd_sbe,
    REC_ADCP:          decode_adcp,
    REC_SIDESCAN:      decode_sidescan,
    REC_ECO:           decode_eco,
    REC_GPS:           decode_gps,
    REC_VEHICLE_NAME:  decode_vehicle_name,
    REC_VEHICLE_INFO:  decode_vehicle_info,
    REC_MANUFACTURER:  decode_manufacturer_info,
    REC_MODEM_LOG:     decode_modem_log,
    REC_DIAGNOSTIC:    decode_diagnostic,
    REC_MISSION_MODES: decode_mission_modes,
    REC_MISSION_LEGS:  decode_mission_legs,
    REC_SENSOR_NAMES:  decode_sensor_names,
    REC_SENSOR_TYPES:  decode_sensor_types,
    REC_SENSOR_DISPLAY: decode_sensor_display,
    REC_NAV_ACOUSTIC:  decode_nav_acoustic,
    REC_DATA_CHANNELS: decode_data_channels,
    REC_WAYPOINTS:     decode_waypoints,
    REC_ECO_CAL:       decode_eco_calibration,
    REC_ACOUSTIC_FIX:  decode_acoustic_fix,
    REC_BATTERY_STATUS: decode_battery_status,
    REC_BATTERY_CELLS:  decode_battery_cells,
    REC_OBJ_NAV:       decode_objective_nav,
    REC_COMPASS_CAL:   decode_compass_cal,
    REC_HOUSING_TEMP:  decode_housing_temp,
    REC_ENERGY_MON:    decode_energy_monitor,
    REC_DVL_STATUS:    decode_dvl_status,
    REC_SUBSYS_MODE:   decode_subsystem_mode,
    REC_STARTUP_FLAG:  decode_startup_flag,
    REC_EVENT_MARKER:  decode_event_marker,
}


def _stamp_by_position(data, target_type, ref_type, ref_t_hrs):
    """Assign timestamps to records that carry no embedded timestamp.

    Scans the raw binary once to record the file-byte offset of every
    target-type and reference-type record, then uses numpy.interp to
    map the reference timestamps onto the target positions.

    Parameters
    ----------
    data : bytes
        Raw file contents.
    target_type : int
        Record type code whose timestamps we want to infer.
    ref_type : int
        Record type code that carries known timestamps (e.g. REC_NAV).
    ref_t_hrs : np.ndarray
        Timestamps for the reference records, in hours from mission start.

    Returns
    -------
    np.ndarray, shape (n_target,)
        Interpolated timestamps in hours.
    """
    ref_pos, target_pos = [], []
    pos = 0
    end = len(data) - HEADER_SIZE
    while pos < end:
        if data[pos] == 0xEB and data[pos + 1] == 0x90:
            _, rtype, plen = struct.unpack_from('<HHH', data, pos + 2)
            payload_end = pos + HEADER_SIZE + plen
            if payload_end <= len(data):
                if rtype == ref_type:
                    ref_pos.append(pos)
                elif rtype == target_type:
                    target_pos.append(pos)
                pos = payload_end
                continue
        pos += 1

    if not ref_pos or not target_pos:
        return np.zeros(len(target_pos))

    return np.interp(
        np.array(target_pos, dtype=np.float64),
        np.array(ref_pos,    dtype=np.float64),
        ref_t_hrs,
    )


def parse_rlf(filepath, decode=True):
    """Parse a REMUS .RLF file.

    Parameters
    ----------
    filepath : str or pathlib.Path
        Path to the .RLF file.
    decode : bool
        If True (default), decode known record types into numpy arrays.
        If False, return only raw payloads.

    Returns
    -------
    dict
        Keys are record type names (str) when decoded, or record type
        integers when raw.  Each value is either a dict of numpy arrays
        (decoded) or a list of bytes objects (raw).

        When decoded, the returned dict also contains a '_raw' key holding
        the full raw records dict and a '_summary' key with record counts.
    """
    with open(filepath, 'rb') as f:
        data = f.read()

    raw = parse_raw_records(data)

    if not decode:
        return raw

    result = {}
    summary = {}
    for rtype, payloads in raw.items():
        name = RECORD_NAMES.get(rtype, f'Unknown_0x{rtype:04x}')
        summary[name] = {
            'type_hex': f'0x{rtype:04x}',
            'count': len(payloads),
            'payload_bytes': len(payloads[0]) if payloads else 0,
        }
        decoder = _DECODERS.get(rtype)
        if decoder is not None:
            result[name] = decoder(payloads)
        else:
            result[name] = payloads  # keep raw

    # Attach inferred timestamps to record types that have no embedded timestamp.
    # Modem log payloads are variable-length strings with no timestamp field;
    # we assign times by interpolating nav record positions in the file.
    nav_decoded = result.get('Navigation')
    modem_decoded = result.get('Acoustic Modem Log')
    if (nav_decoded is not None and modem_decoded is not None
            and isinstance(modem_decoded, dict)):
        modem_decoded['t_hrs'] = _stamp_by_position(
            data, REC_MODEM_LOG, REC_NAV, nav_decoded['t_hrs'])

    result['_raw'] = raw
    result['_summary'] = summary
    return result


def print_summary(parsed):
    """Print a summary table of record types found in a parsed RLF file."""
    summary = parsed.get('_summary', {})
    print(f"{'Record Type':<28} {'Hex':>8} {'Count':>10} {'Payload':>8}")
    print('-' * 58)
    for name in sorted(summary, key=lambda k: summary[k]['count'], reverse=True):
        s = summary[name]
        print(f"  {name:<26} {s['type_hex']:>8} {s['count']:>10} {s['payload_bytes']:>6} B")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys
    import os

    if len(sys.argv) < 2:
        print(f"Usage: python {os.path.basename(__file__)} <file.RLF> [--plot]")
        print(f"       Parse a REMUS .RLF file and print a summary.")
        print(f"       Add --plot to generate a diagnostic plot.")
        sys.exit(1)

    filepath = sys.argv[1]
    do_plot = '--plot' in sys.argv

    print(f"Parsing: {filepath}")
    print(f"Size: {os.path.getsize(filepath) / 1e6:.1f} MB")
    print()

    parsed = parse_rlf(filepath)
    print_summary(parsed)

    # Quick stats for decoded types
    for name in ['Navigation', 'YSI CTD', 'Seabird CTD (SBE49)', 'ADCP/DVL (1200 kHz)']:
        data = parsed.get(name)
        if data is None or isinstance(data, list):
            continue
        print(f"\n--- {name} ---")
        for key, arr in data.items():
            if isinstance(arr, np.ndarray) and arr.dtype.kind == 'f' and key not in ('ts_raw',):
                finite = arr[np.isfinite(arr)]
                if len(finite) > 0:
                    print(f"  {key:<20} min={np.min(finite):12.3f}  "
                          f"max={np.max(finite):12.3f}  "
                          f"mean={np.mean(finite):10.3f}")

    if do_plot:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        # Style
        try:
            plt.style.use('seaborn-v0_8-whitegrid')
        except OSError:
            try:
                plt.style.use('seaborn-whitegrid')
            except OSError:
                pass

        TITLE_FS = 11
        LABEL_FS = 9
        TICK_FS  = 8
        LW       = 0.7

        veh_name = (parsed.get('Vehicle Name') or {}).get('name', 'REMUS-100')
        base = os.path.basename(filepath)
        fig, axes = plt.subplots(4, 2, figsize=(16, 18),
                                 constrained_layout=True)
        fig.suptitle(f'REMUS-100 "{veh_name}" — {base}',
                     fontsize=13, fontweight='bold')

        nav  = parsed.get('Navigation')
        adcp = parsed.get('ADCP/DVL (1200 kHz)')
        ctd  = parsed.get('YSI CTD')
        sbe  = parsed.get('Seabird CTD (SBE49)')
        eco  = parsed.get('Wetlabs ECO BB2F')
        ss   = parsed.get('Sidescan (900 kHz)')

        # ── Panel 1 (0,0): AUV track colored by depth ────────────────────────
        ax = axes[0, 0]
        if nav is not None:
            d = nav['depth']
            vmax = float(np.nanpercentile(d[d > 0], 98)) if np.any(d > 0) else 10.0
            sc = ax.scatter(nav['lon'], nav['lat'], c=d, cmap='plasma_r',
                            s=0.4, alpha=0.55, vmin=0, vmax=vmax)
            cb = fig.colorbar(sc, ax=ax, shrink=0.85, pad=0.02)
            cb.set_label('Depth (m)', fontsize=LABEL_FS)
            cb.ax.tick_params(labelsize=TICK_FS)
            wps = parsed.get('Waypoints') or []
            if wps:
                ax.scatter([w['lon'] for w in wps], [w['lat'] for w in wps],
                           marker='^', s=40, c='yellow', edgecolors='k',
                           linewidths=0.7, zorder=5, label='Waypoints')
                ax.legend(fontsize=TICK_FS, loc='best', framealpha=0.7)
            ax.set_xlabel('Longitude', fontsize=LABEL_FS)
            ax.set_ylabel('Latitude', fontsize=LABEL_FS)
            ax.set_title('AUV Track', fontsize=TITLE_FS)
            ax.set_aspect('equal')
            ax.ticklabel_format(useOffset=False)
            ax.tick_params(labelsize=TICK_FS)
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=20, ha='right')
        else:
            ax.set_visible(False)

        # ── Panel 2 (0,1): Depth profile vs time ─────────────────────────────
        ax = axes[0, 1]
        if nav is not None:
            t = nav['t_hrs']
            d = nav['depth']
            ax.fill_between(t, d, 0, alpha=0.30, color='steelblue')
            ax.plot(t, d, color='steelblue', lw=LW, label='Vehicle')
            if adcp is not None:
                adcp_t = np.linspace(t[0], t[-1], len(adcp['depth']))
                bottom = adcp['depth'] + np.clip(adcp['altitude'], 0, 50)
                valid = np.isfinite(bottom) & (adcp['altitude'] > 0) & (adcp['altitude'] < 40)
                if np.any(valid):
                    bmax = float(np.nanmax(bottom[valid]))
                    ax.fill_between(adcp_t[valid], bottom[valid], bmax + 0.5,
                                    alpha=0.25, color='saddlebrown')
                    ax.plot(adcp_t[valid], bottom[valid],
                            color='saddlebrown', lw=LW, label='Seafloor')
            ax.set_xlabel('Time (hours)', fontsize=LABEL_FS)
            ax.set_ylabel('Depth (m)', fontsize=LABEL_FS)
            ax.set_title('Depth Profile', fontsize=TITLE_FS)
            ax.invert_yaxis()
            ax.legend(fontsize=TICK_FS, framealpha=0.7)
            ax.tick_params(labelsize=TICK_FS)
        else:
            ax.set_visible(False)

        # ── Panel 3 (1,0): Temperature & Salinity ────────────────────────────
        ax = axes[1, 0]
        if ctd is not None:
            step = max(1, len(ctd['t_hrs']) // 5000)
            t = ctd['t_hrs'][::step]
            C_T = '#c0392b'
            C_S = '#2471a3'
            ax.plot(t, ctd['temperature'][::step], color=C_T, lw=LW,
                    alpha=0.85, label='Temperature (°C)')
            ax.set_xlabel('Time (hours)', fontsize=LABEL_FS)
            ax.set_ylabel('Temperature (°C)', fontsize=LABEL_FS, color=C_T)
            ax.tick_params(axis='y', colors=C_T, labelsize=TICK_FS)
            ax.tick_params(axis='x', labelsize=TICK_FS)
            ax2 = ax.twinx()
            ax2.plot(t, ctd['salinity'][::step], color=C_S, lw=LW,
                     alpha=0.85, label='Salinity (PSU)')
            ax2.set_ylabel('Salinity (PSU)', fontsize=LABEL_FS, color=C_S)
            ax2.tick_params(axis='y', colors=C_S, labelsize=TICK_FS)
            ax.set_title('Temperature & Salinity (YSI CTD)', fontsize=TITLE_FS)
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, fontsize=TICK_FS,
                      loc='best', framealpha=0.7)
        else:
            ax.set_visible(False)

        # ── Panel 4 (1,1): Speed of sound ────────────────────────────────────
        ax = axes[1, 1]
        plotted_sos = False
        if ctd is not None:
            step = max(1, len(ctd['t_hrs']) // 5000)
            ax.plot(ctd['t_hrs'][::step], ctd['sound_speed'][::step],
                    color='steelblue', lw=LW, alpha=0.85, label='YSI CTD')
            plotted_sos = True
        if sbe is not None and 't_hrs' in sbe:
            step = max(1, len(sbe['t_hrs']) // 2000)
            ax.plot(sbe['t_hrs'][::step], sbe['sound_speed'][::step],
                    color='tomato', lw=LW + 0.3, alpha=0.85, label='Seabird SBE49')
            plotted_sos = True
        if plotted_sos:
            ax.set_xlabel('Time (hours)', fontsize=LABEL_FS)
            ax.set_ylabel('Speed of Sound (m/s)', fontsize=LABEL_FS)
            ax.set_title('Speed of Sound', fontsize=TITLE_FS)
            ax.legend(fontsize=TICK_FS, framealpha=0.7)
            ax.tick_params(labelsize=TICK_FS)
        else:
            ax.set_visible(False)

        # ── Panel 5 (2,0): ECO — Chlorophyll & Backscatter ───────────────────
        ax = axes[2, 0]
        if eco is not None and 't_hrs' in eco:
            step = max(1, len(eco['t_hrs']) // 5000)
            t = eco['t_hrs'][::step]
            C_CHL = '#1e8449'
            C_BB  = '#6c3483'
            ax.plot(t, eco['chlorophyll'][::step], color=C_CHL, lw=LW,
                    alpha=0.85, label='Chlorophyll (μg/L)')
            ax.set_xlabel('Time (hours)', fontsize=LABEL_FS)
            ax.set_ylabel('Chlorophyll (μg/L)', fontsize=LABEL_FS, color=C_CHL)
            ax.tick_params(axis='y', colors=C_CHL, labelsize=TICK_FS)
            ax.tick_params(axis='x', labelsize=TICK_FS)
            ax2 = ax.twinx()
            ax2.plot(t, eco['beta470'][::step], color=C_BB, lw=LW,
                     alpha=0.75, label='β₄₇₀ (1/m/sr)')
            ax2.set_ylabel('β₄₇₀ (1/m/sr)', fontsize=LABEL_FS, color=C_BB)
            ax2.tick_params(axis='y', colors=C_BB, labelsize=TICK_FS)
            ax.set_title('Wetlabs ECO BB2F', fontsize=TITLE_FS)
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, fontsize=TICK_FS,
                      loc='best', framealpha=0.7)
        else:
            ax.set_visible(False)

        # ── Panel 6 (2,1): Vehicle attitude — Heading, Pitch, Roll ───────────
        ax = axes[2, 1]
        if adcp is not None and nav is not None:
            t_a = np.linspace(nav['t_hrs'][0], nav['t_hrs'][-1], len(adcp['heading']))
            C_H = 'navy'
            C_P = 'darkorange'
            C_R = '#8e44ad'
            ax.plot(t_a, adcp['heading'], color=C_H, lw=LW, alpha=0.55,
                    label='Heading (°)')
            ax.set_xlabel('Time (hours)', fontsize=LABEL_FS)
            ax.set_ylabel('Heading (°)', fontsize=LABEL_FS, color=C_H)
            ax.tick_params(axis='y', colors=C_H, labelsize=TICK_FS)
            ax.tick_params(axis='x', labelsize=TICK_FS)
            ax2 = ax.twinx()
            ax2.plot(t_a, adcp['pitch'], color=C_P, lw=LW, alpha=0.75,
                     label='Pitch (°)')
            ax2.plot(t_a, adcp['roll'],  color=C_R, lw=LW, alpha=0.75,
                     label='Roll (°)')
            ax2.set_ylabel('Pitch / Roll (°)', fontsize=LABEL_FS)
            ax2.tick_params(labelsize=TICK_FS)
            ax.set_title('Vehicle Attitude', fontsize=TITLE_FS)
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, fontsize=TICK_FS, framealpha=0.7)
        else:
            ax.set_visible(False)

        # ── Panel 7 (3,0): Sidescan bathymetry ───────────────────────────────
        ax = axes[3, 0]
        if ss is not None:
            alt = ss['altitude']
            dep = ss['depth']
            valid = np.isfinite(alt) & np.isfinite(dep) & (alt > 0) & (alt < 30)
            if np.any(valid):
                bd = dep[valid] + alt[valid]
                sc = ax.scatter(ss['lon'][valid], ss['lat'][valid],
                                c=bd, cmap='Blues_r', s=1.0, alpha=0.7,
                                vmin=float(np.nanpercentile(bd, 2)),
                                vmax=float(np.nanpercentile(bd, 98)))
                cb = fig.colorbar(sc, ax=ax, shrink=0.85, pad=0.02)
                cb.set_label('Water Depth (m)', fontsize=LABEL_FS)
                cb.ax.tick_params(labelsize=TICK_FS)
                ax.set_xlabel('Longitude', fontsize=LABEL_FS)
                ax.set_ylabel('Latitude', fontsize=LABEL_FS)
                ax.set_title('Sidescan Bathymetry', fontsize=TITLE_FS)
                ax.set_aspect('equal')
                ax.ticklabel_format(useOffset=False)
                ax.tick_params(labelsize=TICK_FS)
                plt.setp(ax.xaxis.get_majorticklabels(), rotation=20, ha='right')
            else:
                ax.set_visible(False)
        else:
            ax.set_visible(False)

        # ── Panel 8 (3,1): Navigation speed ──────────────────────────────────
        ax = axes[3, 1]
        if nav is not None:
            t = nav['t_hrs']
            spd = nav['speed']
            # Clip to plausible vehicle speeds (0–3 m/s)
            spd_clipped = np.where((spd >= 0) & (spd <= 3), spd, np.nan)
            step = max(1, len(t) // 5000)
            ax.plot(t[::step], spd_clipped[::step], color='teal', lw=LW,
                    alpha=0.8)
            ax.set_xlabel('Time (hours)', fontsize=LABEL_FS)
            ax.set_ylabel('Speed (m/s)', fontsize=LABEL_FS)
            ax.set_title('Navigation Speed', fontsize=TITLE_FS)
            ax.tick_params(labelsize=TICK_FS)
            ax.set_ylim(bottom=0)
        else:
            ax.set_visible(False)

        outpath = filepath.rsplit('.', 1)[0] + '_summary.png'
        plt.savefig(outpath, dpi=150, bbox_inches='tight')
        print(f"\nPlot saved: {outpath}")

        # ── Data Quality Figure ───────────────────────────────────────────────
        fig_q, axes_q = plt.subplots(4, 1, figsize=(14, 13),
                                     constrained_layout=True)
        fig_q.suptitle(f'REMUS-100 "{veh_name}" — {base} — Data Quality',
                       fontsize=13, fontweight='bold')

        # Panel Q1: Sensor record rate (records/minute) — gaps = dropouts
        ax = axes_q[0]
        if nav is not None:
            t_end = nav['t_hrs'][-1]
            bin_w = 1.0 / 60.0  # 1-minute bins
            t_bins = np.arange(0, t_end + bin_w, bin_w)
            t_centers = (t_bins[:-1] + t_bins[1:]) / 2

            nav_rate, _ = np.histogram(nav['t_hrs'], bins=t_bins)
            ax.plot(t_centers, nav_rate, color='steelblue', lw=LW + 0.3,
                    alpha=0.85, label='Navigation (~18 Hz)')
            if ctd is not None:
                ctd_rate, _ = np.histogram(ctd['t_hrs'], bins=t_bins)
                ax.plot(t_centers, ctd_rate, color='tomato', lw=LW + 0.3,
                        alpha=0.75, label='YSI CTD (~18 Hz)')
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = [], []
            if eco is not None and 't_hrs' in eco:
                eco_rate, _ = np.histogram(eco['t_hrs'], bins=t_bins)
                ax_eco = ax.twinx()
                ax_eco.plot(t_centers, eco_rate, color='#1e8449', lw=LW + 0.3,
                            alpha=0.75, label='ECO BB2F (~1 Hz)')
                ax_eco.set_ylabel('ECO Records / min', fontsize=LABEL_FS,
                                  color='#1e8449')
                ax_eco.tick_params(axis='y', colors='#1e8449', labelsize=TICK_FS)
                h2, l2 = ax_eco.get_legend_handles_labels()
            ax.set_xlabel('Time (hours)', fontsize=LABEL_FS)
            ax.set_ylabel('Records per Minute', fontsize=LABEL_FS)
            ax.set_title('Sensor Record Rate — gaps or drops indicate sensor dropouts',
                         fontsize=TITLE_FS)
            ax.legend(h1 + h2, l1 + l2, fontsize=TICK_FS, framealpha=0.7)
            ax.tick_params(labelsize=TICK_FS)
            ax.set_xlim(0, t_end)
        else:
            ax.set_visible(False)

        # Panel Q2: DVL bottom lock fraction + acoustic nav fix markers
        ax = axes_q[1]
        if adcp is not None and nav is not None:
            t_end = nav['t_hrs'][-1]
            adcp_t = np.linspace(0, t_end, len(adcp['altitude']))
            valid_alt = (np.isfinite(adcp['altitude']) &
                         (adcp['altitude'] > 0) & (adcp['altitude'] < 40))
            # 5-minute rolling fraction
            win = max(1, int(round(5.0 / 60.0 / t_end * len(valid_alt))))
            kernel = np.ones(win) / win
            rolling = np.convolve(valid_alt.astype(float), kernel, mode='same')
            ax.fill_between(adcp_t, rolling, alpha=0.25, color='steelblue')
            ax.plot(adcp_t, rolling, color='steelblue', lw=LW + 0.3,
                    label='DVL bottom lock (5-min rolling)')
            ax.set_ylim(0, 1.05)
            ax.yaxis.set_major_formatter(
                matplotlib.ticker.FuncFormatter(lambda v, _: f'{v:.0%}'))

            # Acoustic nav fix event markers
            acoustic_fix = parsed.get('Acoustic Nav Fix')
            if acoustic_fix and len(acoustic_fix['datetime']) > 0:
                nav_start_utc_hrs = float(nav['ts_raw'][0] & 0x7FFFFFFF) / 3_600_000.0
                fix_t = []
                for dt_str in acoustic_fix['datetime']:
                    try:
                        hh = int(dt_str[11:13])
                        mm_v = int(dt_str[14:16])
                        ss = int(dt_str[17:19])
                        delta = (hh + mm_v / 60.0 + ss / 3600.0) - nav_start_utc_hrs
                        if delta < -12:
                            delta += 24
                        fix_t.append(delta)
                    except Exception:
                        pass
                fix_t = [f for f in fix_t if 0 <= f <= t_end]
                for ft in fix_t:
                    ax.axvline(ft, color='crimson', lw=0.8, alpha=0.55, zorder=3)
                if fix_t:
                    ax.axvline(fix_t[0], color='crimson', lw=0.8, alpha=0.8,
                               label=f'Acoustic nav fix (n={len(fix_t)})', zorder=3)
            ax.set_xlabel('Time (hours)', fontsize=LABEL_FS)
            ax.set_ylabel('Bottom Lock', fontsize=LABEL_FS)
            ax.set_title('DVL Bottom Lock & Acoustic Navigation Fixes',
                         fontsize=TITLE_FS)
            ax.legend(fontsize=TICK_FS, framealpha=0.7)
            ax.tick_params(labelsize=TICK_FS)
            ax.set_xlim(0, t_end)
        else:
            ax.set_visible(False)

        # Panel Q3: Battery pack voltage (time is approximate — uniform spacing)
        ax = axes_q[2]
        battery_status = parsed.get('Battery Status')
        if battery_status is not None and nav is not None:
            t_end = nav['t_hrs'][-1]
            n_recs = len(battery_status)
            batt_t = np.linspace(0, t_end, n_recs)
            batt_ids = np.array([r['batt_id'] for r in battery_status])
            pack_mv = np.array([r.get('pack_mv', np.nan) for r in battery_status])
            colors_b = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12']
            for k, bid in enumerate(np.unique(batt_ids)):
                mask = batt_ids == bid
                ax.plot(batt_t[mask], pack_mv[mask] / 1000.0,
                        'o-', ms=4, lw=1.2, color=colors_b[k % 4],
                        label=f'Bank {bid}')
            ax.set_xlabel('Time (hours, approx.)', fontsize=LABEL_FS)
            ax.set_ylabel('Pack Voltage (V)', fontsize=LABEL_FS)
            ax.set_title('Smart Battery Pack Voltage (timestamps are approximate)',
                         fontsize=TITLE_FS)
            ax.legend(fontsize=TICK_FS, framealpha=0.7)
            ax.tick_params(labelsize=TICK_FS)
            ax.set_xlim(0, t_end)
        else:
            ax.set_visible(False)

        # Panel Q4: Acoustic modem receive quality scores
        ax = axes_q[3]
        modem_log = parsed.get('Acoustic Modem Log')
        if (modem_log is not None and isinstance(modem_log, dict)
                and 't_hrs' in modem_log and nav is not None):
            import re as _re
            _qual_pat = _re.compile(r'Data quality: \(\d+\) (\d+)')
            q_t, q_scores = [], []
            for msg, t in zip(modem_log['message'], modem_log['t_hrs']):
                m = _qual_pat.match(msg)
                if m:
                    q_t.append(t)
                    q_scores.append(int(m.group(1)))
            if q_t:
                t_end = nav['t_hrs'][-1]
                ax.scatter(q_t, q_scores, s=18, color='steelblue',
                           alpha=0.8, zorder=3)
                ax.set_xlabel('Time (hours)', fontsize=LABEL_FS)
                ax.set_ylabel('Quality Score', fontsize=LABEL_FS)
                ax.set_title(
                    f'Acoustic Modem Receive Quality (n={len(q_t)} receptions)',
                    fontsize=TITLE_FS)
                ax.tick_params(labelsize=TICK_FS)
                ax.set_xlim(0, t_end)
                ax.set_ylim(0, 210)
            else:
                ax.text(0.5, 0.5, 'No modem quality messages found',
                        ha='center', va='center', transform=ax.transAxes,
                        fontsize=LABEL_FS)
                ax.set_title('Acoustic Modem Receive Quality', fontsize=TITLE_FS)
        else:
            ax.set_visible(False)

        qpath = filepath.rsplit('.', 1)[0] + '_quality.png'
        fig_q.savefig(qpath, dpi=150, bbox_inches='tight')
        print(f"Plot saved: {qpath}")
        plt.close('all')
