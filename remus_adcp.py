"""
REMUS ADCP Data File Parser
============================
Parses the companion data files alongside the RLF:

  .ADC  — RDI PD0 binary ADCP ensemble data (standard Teledyne RDI format)
  .GPS  — ASCII position log (one fix per line, ~1 Hz)
  .txt  — RDI ADCP startup command file
  .rmf  — REMUS Run Mission File (INI-style mission plan)
  .ini  — REMUS vehicle configuration (INI-style, parsed by remus_rlf.py docs)

The .ADC file is the primary scientific data file containing per-bin velocity
profiles, correlation, echo intensity, percent-good, and bottom-track data
from the onboard RDI 1200 kHz DVL.

RDI PD0 Format Reference:
  Teledyne RDI "WorkHorse Commands and Output Data Format" (P/N 957-6156-00)
  Available from Teledyne Marine: https://www.teledynemarine.com
"""

import struct
import re
import numpy as np
from pathlib import Path


# ---------------------------------------------------------------------------
# .ADC file — RDI PD0 binary ADCP data
# ---------------------------------------------------------------------------

# PD0 Data Type IDs
PD0_FIXED_LEADER   = 0x0000
PD0_VARIABLE_LEADER = 0x0080
PD0_VELOCITY        = 0x0100
PD0_CORRELATION     = 0x0200
PD0_ECHO_INTENSITY  = 0x0300
PD0_PERCENT_GOOD    = 0x0400
PD0_BOTTOM_TRACK    = 0x0600

PD0_TYPE_NAMES = {
    PD0_FIXED_LEADER:    'Fixed Leader',
    PD0_VARIABLE_LEADER: 'Variable Leader',
    PD0_VELOCITY:        'Velocity',
    PD0_CORRELATION:     'Correlation',
    PD0_ECHO_INTENSITY:  'Echo Intensity',
    PD0_PERCENT_GOOD:    'Percent-Good',
    PD0_BOTTOM_TRACK:    'Bottom Track',
    0x2000:              'REMUS Navigation (custom)',
}

FREQ_MAP = {0: 75, 1: 150, 2: 300, 3: 600, 4: 1200, 5: 2400}
COORD_MAP = {0b00: 'Beam', 0b01: 'Instrument', 0b10: 'Ship', 0b11: 'Earth'}


def _parse_fixed_leader(data, offset):
    """Parse PD0 Fixed Leader data type."""
    o = offset
    return {
        'fw_version':     data[o + 2],
        'fw_revision':    data[o + 3],
        'sys_config':     struct.unpack_from('<H', data, o + 4)[0],
        'frequency_khz':  FREQ_MAP.get(struct.unpack_from('<H', data, o + 4)[0] & 0x07, 0),
        'beam_angle':     20 if ((struct.unpack_from('<H', data, o + 4)[0] >> 4) & 0x03) == 0 else 30,
        'n_beams':        4 if not ((struct.unpack_from('<H', data, o + 4)[0] >> 4) & 0x01) else 5,
        'orientation':    'Up' if (struct.unpack_from('<H', data, o + 4)[0] >> 7) & 0x01 else 'Down',
        'n_cells':        data[o + 9],
        'pings_per_ens':  struct.unpack_from('<H', data, o + 10)[0],
        'cell_size_cm':   struct.unpack_from('<H', data, o + 12)[0],
        'blank_cm':       struct.unpack_from('<H', data, o + 14)[0],
        'coord_transform': COORD_MAP.get((data[o + 25] >> 3) & 0x03, 'Unknown'),
    }


def _parse_variable_leader(data, offset):
    """Parse PD0 Variable Leader data type."""
    o = offset
    ens_lo = struct.unpack_from('<H', data, o + 2)[0]
    year   = data[o + 4]
    month  = data[o + 5]
    day    = data[o + 6]
    hour   = data[o + 7]
    minute = data[o + 8]
    second = data[o + 9]
    hsec   = data[o + 10]
    # High byte of ensemble number at offset 11
    ens_hi = data[o + 11]
    ens_num = ens_lo + (ens_hi << 16)

    return {
        'ensemble_number': ens_num,
        'year':     2000 + year if year < 100 else year,
        'month':    month,
        'day':      day,
        'hour':     hour,
        'minute':   minute,
        'second':   second,
        'hundredths': hsec,
        'depth_dm':       struct.unpack_from('<H', data, o + 14)[0],
        'heading_cdeg':   struct.unpack_from('<H', data, o + 18)[0],
        'pitch_cdeg':     struct.unpack_from('<h', data, o + 20)[0],
        'roll_cdeg':      struct.unpack_from('<h', data, o + 22)[0],
        'salinity_ppt':   struct.unpack_from('<H', data, o + 24)[0],
        'temperature_cdeg': struct.unpack_from('<h', data, o + 26)[0],
    }


def _parse_velocity(data, offset, n_cells):
    """Parse PD0 Velocity data type. Returns (n_cells, 4) array in mm/s."""
    o = offset + 2  # skip type ID
    vel = np.empty((n_cells, 4), dtype=np.int16)
    for c in range(n_cells):
        for b in range(4):
            vel[c, b] = struct.unpack_from('<h', data, o + (c * 4 + b) * 2)[0]
    return vel


def _parse_echo_intensity(data, offset, n_cells):
    """Parse PD0 Echo Intensity data type. Returns (n_cells, 4) array in counts."""
    o = offset + 2
    echo = np.empty((n_cells, 4), dtype=np.uint8)
    for c in range(n_cells):
        for b in range(4):
            echo[c, b] = data[o + c * 4 + b]
    return echo


def _parse_correlation(data, offset, n_cells):
    """Parse PD0 Correlation Magnitude data type. Returns (n_cells, 4) array."""
    o = offset + 2
    corr = np.empty((n_cells, 4), dtype=np.uint8)
    for c in range(n_cells):
        for b in range(4):
            corr[c, b] = data[o + c * 4 + b]
    return corr


def _parse_percent_good(data, offset, n_cells):
    """Parse PD0 Percent-Good data type. Returns (n_cells, 4) array."""
    o = offset + 2
    pg = np.empty((n_cells, 4), dtype=np.uint8)
    for c in range(n_cells):
        for b in range(4):
            pg[c, b] = data[o + c * 4 + b]
    return pg


def _parse_bottom_track(data, offset):
    """Parse PD0 Bottom Track data type (key fields)."""
    o = offset
    bt = {
        'bt_pings_per_ens':  struct.unpack_from('<H', data, o + 2)[0],
        'bt_range_cm':       [struct.unpack_from('<H', data, o + 16 + 2 * b)[0] for b in range(4)],
        'bt_velocity_mms':   [struct.unpack_from('<h', data, o + 24 + 2 * b)[0] for b in range(4)],
        'bt_correlation':    [data[o + 32 + b] for b in range(4)],
        'bt_eval_amp':       [data[o + 36 + b] for b in range(4)],
        'bt_percent_good':   [data[o + 40 + b] for b in range(4)],
    }
    return bt


def parse_adc(filepath):
    """Parse an .ADC file (RDI PD0 format) into structured ensemble data.

    Parameters
    ----------
    filepath : str or Path
        Path to the .ADC file.

    Returns
    -------
    dict with keys:
        'fixed_leader' : dict — instrument configuration (from first ensemble)
        'n_ensembles'  : int
        'n_cells'      : int
        'time'         : dict of arrays (year, month, day, hour, minute, second, hundredths)
        'ensemble_number' : array (int32)
        'heading'      : array (float32, degrees)
        'pitch'        : array (float32, degrees)
        'roll'         : array (float32, degrees)
        'temperature'  : array (float32, deg C)
        'depth'        : array (float32, m, transducer depth)
        'salinity'     : array (int16, ppt)
        'velocity'     : array (n_ens, n_cells, 4) int16, mm/s
                         -32768 = bad/missing data
                         beams: [vel1, vel2, vel3, vel4] in configured coords
        'echo_intensity' : array (n_ens, n_cells, 4) uint8, counts
        'correlation'    : array (n_ens, n_cells, 4) uint8, counts
        'percent_good'   : array (n_ens, n_cells, 4) uint8, percent
        'bottom_track'   : dict of arrays (range, velocity, etc.)
    """
    with open(filepath, 'rb') as f:
        data = f.read()

    # First pass: locate all ensembles and get fixed leader
    ensembles = []
    pos = 0
    fixed_leader = None
    while pos + 6 < len(data):
        if data[pos] == 0x7F and data[pos + 1] == 0x7F:
            ens_bytes = struct.unpack_from('<H', data, pos + 2)[0]
            ens_size = ens_bytes + 2  # payload + checksum
            if pos + ens_size > len(data):
                break
            ensembles.append(pos)
            if fixed_leader is None:
                n_dt = data[pos + 5]
                for j in range(n_dt):
                    dt_off = struct.unpack_from('<H', data, pos + 6 + 2 * j)[0]
                    dt_id = struct.unpack_from('<H', data, pos + dt_off)[0]
                    if dt_id == PD0_FIXED_LEADER:
                        fixed_leader = _parse_fixed_leader(data, pos + dt_off)
                        break
            pos += ens_size
        else:
            pos += 1

    if fixed_leader is None:
        raise ValueError("No valid PD0 ensemble found")

    n_ens = len(ensembles)
    n_cells = fixed_leader['n_cells']

    # Allocate output arrays
    out = {
        'fixed_leader': fixed_leader,
        'n_ensembles': n_ens,
        'n_cells': n_cells,
        'ensemble_number': np.empty(n_ens, dtype=np.int32),
        'year':    np.empty(n_ens, dtype=np.int16),
        'month':   np.empty(n_ens, dtype=np.int8),
        'day':     np.empty(n_ens, dtype=np.int8),
        'hour':    np.empty(n_ens, dtype=np.int8),
        'minute':  np.empty(n_ens, dtype=np.int8),
        'second':  np.empty(n_ens, dtype=np.int8),
        'hundredths': np.empty(n_ens, dtype=np.int8),
        'heading': np.empty(n_ens, dtype=np.float32),
        'pitch':   np.empty(n_ens, dtype=np.float32),
        'roll':    np.empty(n_ens, dtype=np.float32),
        'temperature': np.empty(n_ens, dtype=np.float32),
        'depth':   np.empty(n_ens, dtype=np.float32),
        'salinity': np.empty(n_ens, dtype=np.int16),
        'velocity':       np.full((n_ens, n_cells, 4), -32768, dtype=np.int16),
        'echo_intensity': np.zeros((n_ens, n_cells, 4), dtype=np.uint8),
        'correlation':    np.zeros((n_ens, n_cells, 4), dtype=np.uint8),
        'percent_good':   np.zeros((n_ens, n_cells, 4), dtype=np.uint8),
        'bt_range_cm':    np.zeros((n_ens, 4), dtype=np.uint16),
        'bt_velocity_mms': np.full((n_ens, 4), -32768, dtype=np.int16),
    }

    # Second pass: extract all data
    for i, ens_pos in enumerate(ensembles):
        n_dt = data[ens_pos + 5]
        for j in range(n_dt):
            dt_off = struct.unpack_from('<H', data, ens_pos + 6 + 2 * j)[0]
            abs_off = ens_pos + dt_off
            dt_id = struct.unpack_from('<H', data, abs_off)[0]

            if dt_id == PD0_VARIABLE_LEADER:
                vl = _parse_variable_leader(data, abs_off)
                out['ensemble_number'][i] = vl['ensemble_number']
                out['year'][i]    = vl['year']
                out['month'][i]   = vl['month']
                out['day'][i]     = vl['day']
                out['hour'][i]    = vl['hour']
                out['minute'][i]  = vl['minute']
                out['second'][i]  = vl['second']
                out['hundredths'][i] = vl['hundredths']
                out['heading'][i] = vl['heading_cdeg'] / 100.0
                out['pitch'][i]   = vl['pitch_cdeg'] / 100.0
                out['roll'][i]    = vl['roll_cdeg'] / 100.0
                out['temperature'][i] = vl['temperature_cdeg'] / 100.0
                out['depth'][i]   = vl['depth_dm'] / 10.0
                out['salinity'][i] = vl['salinity_ppt']

            elif dt_id == PD0_VELOCITY:
                out['velocity'][i] = _parse_velocity(data, abs_off, n_cells)

            elif dt_id == PD0_ECHO_INTENSITY:
                out['echo_intensity'][i] = _parse_echo_intensity(data, abs_off, n_cells)

            elif dt_id == PD0_CORRELATION:
                out['correlation'][i] = _parse_correlation(data, abs_off, n_cells)

            elif dt_id == PD0_PERCENT_GOOD:
                out['percent_good'][i] = _parse_percent_good(data, abs_off, n_cells)

            elif dt_id == PD0_BOTTOM_TRACK:
                bt = _parse_bottom_track(data, abs_off)
                out['bt_range_cm'][i] = bt['bt_range_cm']
                out['bt_velocity_mms'][i] = bt['bt_velocity_mms']

    return out


# ---------------------------------------------------------------------------
# .GPS file — ASCII position log
# ---------------------------------------------------------------------------

_GPS_PATTERN = re.compile(
    r'^G\s+([0-9A-Fa-f]+),\s*'       # ensemble hex counter
    r'(\d+):(\d+):\s*([\d.]+),\s*'    # HH:MM:SS.s
    r'(\d+)([WE])([\d.]+)\s+'         # lon: deg, dir, minutes
    r'(\d+)([NS])([\d.]+)\s*$'        # lat: deg, dir, minutes
)


def parse_gps(filepath):
    """Parse a .GPS ASCII position log file.

    Format: G <hex_counter>, HH:MM:SS.s, <lon_deg><W/E><lon_min>  <lat_deg><N/S><lat_min>

    Parameters
    ----------
    filepath : str or Path

    Returns
    -------
    dict with keys:
        'ensemble_hex' : list of str
        'hour', 'minute', 'second' : arrays
        'lat', 'lon' : arrays (float64, degrees; W and S are negative)
    """
    ensembles = []
    hours = []
    minutes = []
    seconds = []
    lats = []
    lons = []

    with open(filepath, 'r') as f:
        for line in f:
            m = _GPS_PATTERN.match(line.strip())
            if m:
                ensembles.append(m.group(1))
                hours.append(int(m.group(2)))
                minutes.append(int(m.group(3)))
                seconds.append(float(m.group(4)))

                lon_deg = int(m.group(5))
                lon_dir = m.group(6)
                lon_min = float(m.group(7))
                lon = lon_deg + lon_min / 60.0
                if lon_dir == 'W':
                    lon = -lon

                lat_deg = int(m.group(8))
                lat_dir = m.group(9)
                lat_min = float(m.group(10))
                lat = lat_deg + lat_min / 60.0
                if lat_dir == 'S':
                    lat = -lat

                lats.append(lat)
                lons.append(lon)

    return {
        'ensemble_hex': ensembles,
        'hour':    np.array(hours, dtype=np.int8),
        'minute':  np.array(minutes, dtype=np.int8),
        'second':  np.array(seconds, dtype=np.float32),
        'lat':     np.array(lats, dtype=np.float64),
        'lon':     np.array(lons, dtype=np.float64),
    }


# ---------------------------------------------------------------------------
# .txt file — ADCP startup commands
# ---------------------------------------------------------------------------

def parse_adcp_config(filepath):
    """Parse an ADCP startup command .txt file.

    Returns a dict mapping RDI command codes to (value, comment) tuples.
    """
    commands = {}
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Split on first # for inline comment
            parts = line.split('#', 1)
            cmd_part = parts[0].strip()
            comment = parts[1].strip() if len(parts) > 1 else ''
            # Command format: XX=value or XXvalue
            if '=' in cmd_part:
                code, val = cmd_part.split('=', 1)
                commands[code.strip()] = {'value': val.strip(), 'comment': comment}
            else:
                # Commands like WV250 without =
                for i, ch in enumerate(cmd_part):
                    if ch.isdigit() or ch == '-':
                        code = cmd_part[:i]
                        val = cmd_part[i:]
                        commands[code] = {'value': val, 'comment': comment}
                        break
    return commands


# ---------------------------------------------------------------------------
# .rmf file — Run Mission File
# ---------------------------------------------------------------------------

def parse_rmf(filepath):
    """Parse a REMUS Run Mission File (.rmf).

    The RMF is an INI-style file with [Location] and [Objective] sections
    that define waypoints, transponders, and mission legs.

    Returns a dict with:
        'locations'  : list of dicts (waypoints, transponders)
        'objectives' : list of dicts (mission legs / commands)
    """
    locations = []
    objectives = []
    current = None
    current_type = None

    with open(filepath, 'r') as f:
        for line in f:
            raw = line.rstrip('\n\r')
            stripped = raw.strip()

            # Section header
            if stripped.startswith('['):
                section = stripped.strip('[]').strip()
                if section == 'Location' or section == 'Location':
                    current = {}
                    current_type = 'location'
                    locations.append(current)
                elif section == 'Objective':
                    current = {}
                    current_type = 'objective'
                    objectives.append(current)
                else:
                    current = {}
                    current_type = section.lower()
                continue

            # Key=Value line
            if '=' in stripped and current is not None:
                # Strip inline REMUS checksums (#$!...)
                clean = stripped.split('#$!')[0].strip()
                if '#' in clean:
                    clean = clean.split('#')[0].strip()
                if '=' in clean:
                    key, val = clean.split('=', 1)
                    current[key.strip()] = val.strip()

    return {
        'locations': locations,
        'objectives': objectives,
    }


# ---------------------------------------------------------------------------
# Convenience: parse entire ADCP directory
# ---------------------------------------------------------------------------

def parse_adcp_directory(dirpath):
    """Parse all files in an ADCP directory.

    Parameters
    ----------
    dirpath : str or Path
        Path to a directory containing .ADC, .GPS, and .txt files.

    Returns
    -------
    dict with keys 'adc', 'gps', 'config' (each may be None if file missing)
    """
    dirpath = Path(dirpath)
    result = {'adc': None, 'gps': None, 'config': None}

    adc_files = list(dirpath.glob('*.ADC'))
    if adc_files:
        result['adc'] = parse_adc(adc_files[0])

    gps_files = list(dirpath.glob('*.GPS'))
    if gps_files:
        result['gps'] = parse_gps(gps_files[0])

    txt_files = list(dirpath.glob('*.txt'))
    if txt_files:
        result['config'] = parse_adcp_config(txt_files[0])

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def plot_adc(adc, gps=None, outpath=None):
    """Generate diagnostic plots from parsed ADC data.

    Parameters
    ----------
    adc : dict — output from parse_adc()
    gps : dict or None — output from parse_gps(), overlaid on track plot
    outpath : str or Path or None — save to file; if None, plt.show()
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize

    fl = adc['fixed_leader']
    n_ens = adc['n_ensembles']
    n_cells = adc['n_cells']
    cell_cm = fl['cell_size_cm']

    # Time axis: seconds since first ensemble
    t = (adc['hour'].astype(np.float64) * 3600 +
         adc['minute'].astype(np.float64) * 60 +
         adc['second'].astype(np.float64) +
         adc['hundredths'].astype(np.float64) / 100.0)
    # Handle midnight rollover
    dt = np.diff(t)
    rollover = np.where(dt < -43200)[0]
    for r in rollover:
        t[r + 1:] += 86400
    t -= t[0]
    t_hours = t / 3600.0

    # Cell center distances from transducer (m)
    blank_m = fl['blank_cm'] / 100.0
    cell_m = cell_cm / 100.0
    bin_centers = blank_m + cell_m * (np.arange(n_cells) + 0.5)

    # Velocity: convert to float, mask bad data
    vel = adc['velocity'].astype(np.float32)
    vel[vel == -32768] = np.nan
    vel_cms = vel / 10.0  # mm/s → cm/s

    # Echo intensity (average of 4 beams)
    echo_avg = adc['echo_intensity'].astype(np.float32).mean(axis=2)

    # Bottom track range (average valid beams, in m)
    bt = adc['bt_range_cm'].astype(np.float32)
    bt[bt == 0] = np.nan
    bt_m = bt / 100.0

    # Speed magnitude from beams 1&2 (horizontal components in ship coords)
    with np.errstate(invalid='ignore'):
        speed_h = np.sqrt(np.nanmean(vel_cms[:, :, 0]**2 + vel_cms[:, :, 1]**2, axis=1))

    # --- Figure layout: 3 rows × 2 cols ---
    fig, axes = plt.subplots(3, 2, figsize=(16, 12))
    date_str = f"{adc['year'][0]}-{adc['month'][0]:02d}-{adc['day'][0]:02d}"
    fig.suptitle(f"REMUS ADCP — {date_str}  |  {fl['frequency_khz']} kHz "
                 f"{fl['orientation']}-facing  |  {n_ens} ensembles, "
                 f"{n_cells}×{cell_cm} cm bins", fontsize=13, fontweight='bold')

    # (0,0) Velocity beam 1 (pcolor)
    ax = axes[0, 0]
    v1 = vel_cms[:, :, 0].T
    vmax = np.nanpercentile(np.abs(vel_cms[:, :, :2]), 99)
    im = ax.pcolormesh(t_hours, bin_centers, v1,
                       cmap='RdBu_r', vmin=-vmax, vmax=vmax,
                       shading='nearest', rasterized=True)
    fig.colorbar(im, ax=ax, label='cm/s')
    ax.set_ylabel('Range from xducer (m)')
    ax.set_title('Velocity — Beam 1 (ship coords)')
    ax.invert_yaxis()

    # (0,1) Velocity beam 2 (pcolor)
    ax = axes[0, 1]
    v2 = vel_cms[:, :, 1].T
    im = ax.pcolormesh(t_hours, bin_centers, v2,
                       cmap='RdBu_r', vmin=-vmax, vmax=vmax,
                       shading='nearest', rasterized=True)
    fig.colorbar(im, ax=ax, label='cm/s')
    ax.set_title('Velocity — Beam 2 (ship coords)')
    ax.invert_yaxis()

    # (1,0) Echo intensity (pcolor)
    ax = axes[1, 0]
    im = ax.pcolormesh(t_hours, bin_centers, echo_avg.T,
                       cmap='viridis', shading='nearest', rasterized=True)
    fig.colorbar(im, ax=ax, label='counts')
    ax.set_ylabel('Range from xducer (m)')
    ax.set_title('Echo Intensity (4-beam mean)')
    # Overlay BT range
    with np.errstate(invalid='ignore'):
        bt_mean = np.nanmean(bt_m, axis=1)
    valid_bt = ~np.isnan(bt_mean)
    ax.plot(t_hours[valid_bt], bt_mean[valid_bt], 'r.', ms=0.3, alpha=0.5, label='BT range')
    ax.legend(loc='upper right', fontsize=8)
    ax.invert_yaxis()

    # (1,1) Heading, pitch, roll
    ax = axes[1, 1]
    ax.plot(t_hours, adc['heading'], '.', ms=0.3, alpha=0.5, label='Heading')
    ax.set_ylabel('Heading (°)', color='C0')
    ax.set_ylim(0, 360)
    ax.set_yticks([0, 90, 180, 270, 360])
    ax2 = ax.twinx()
    ax2.plot(t_hours, adc['pitch'], '.', ms=0.3, alpha=0.5, color='C1', label='Pitch')
    ax2.plot(t_hours, adc['roll'], '.', ms=0.3, alpha=0.5, color='C2', label='Roll')
    ax2.set_ylabel('Pitch / Roll (°)')
    ax2.set_ylim(-30, 30)
    ax.set_title('Vehicle Attitude')
    # Combined legend
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc='upper right', fontsize=8, markerscale=10)

    # (2,0) Temperature + depth
    ax = axes[2, 0]
    ax.plot(t_hours, adc['temperature'], '.', ms=0.3, alpha=0.5, color='C3')
    ax.set_ylabel('Temperature (°C)', color='C3')
    ax.set_xlabel('Time (hours)')
    ax3 = ax.twinx()
    ax3.plot(t_hours, adc['depth'], '.', ms=0.3, alpha=0.5, color='C4')
    ax3.set_ylabel('Transducer depth (m)', color='C4')
    ax3.invert_yaxis()
    ax.set_title('Temperature & Depth')

    # (2,1) GPS track or BT range time series
    ax = axes[2, 1]
    if gps is not None and len(gps['lat']) > 0:
        ax.plot(gps['lon'], gps['lat'], '.', ms=0.3, alpha=0.3, color='C0', label='GPS')
        ax.set_xlabel('Longitude')
        ax.set_ylabel('Latitude')
        ax.set_title('GPS Track')
        ax.set_aspect('equal')
        ax.ticklabel_format(useOffset=False)
    else:
        # BT range per beam
        for b in range(4):
            valid = bt_m[:, b] > 0
            ax.plot(t_hours[valid], bt_m[valid, b], '.', ms=0.3, alpha=0.3, label=f'Beam {b+1}')
        ax.set_xlabel('Time (hours)')
        ax.set_ylabel('BT Range (m)')
        ax.set_title('Bottom Track Range')
        ax.legend(fontsize=8, markerscale=10)
        ax.invert_yaxis()

    fig.tight_layout()

    if outpath:
        fig.savefig(outpath, dpi=150, bbox_inches='tight')
        print(f"  Saved plot: {outpath}")
    else:
        plt.show()
    plt.close(fig)


if __name__ == '__main__':
    import sys
    import os

    do_plot = '--plot' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]

    if len(args) < 1:
        print(f"Usage: python {os.path.basename(__file__)} <path> [--plot]")
        print(f"  <path> can be an .ADC, .GPS, .txt, or .rmf file,")
        print(f"  or a directory containing these files.")
        print(f"  --plot  Generate diagnostic plot (ADC files only)")
        sys.exit(1)

    path = Path(args[0])

    if path.is_dir():
        # Parse ADCP directory
        print(f"Parsing directory: {path}")
        result = parse_adcp_directory(path)

        if result['config']:
            print(f"\nADCP Config (.txt):")
            for cmd, info in result['config'].items():
                print(f"  {cmd:6s} = {info['value']:10s}  {info['comment']}")

        if result['gps']:
            gps = result['gps']
            print(f"\nGPS Log (.GPS): {len(gps['lat'])} fixes")
            print(f"  Time: {gps['hour'][0]:02d}:{gps['minute'][0]:02d}:{gps['second'][0]:05.2f}"
                  f" → {gps['hour'][-1]:02d}:{gps['minute'][-1]:02d}:{gps['second'][-1]:05.2f}")
            print(f"  Lat:  {gps['lat'].min():.6f} to {gps['lat'].max():.6f}")
            print(f"  Lon:  {gps['lon'].min():.6f} to {gps['lon'].max():.6f}")

        if result['adc']:
            adc = result['adc']
            fl = adc['fixed_leader']
            print(f"\nADCP Data (.ADC): {adc['n_ensembles']} ensembles, "
                  f"{adc['n_cells']} cells × {fl['cell_size_cm']} cm")
            print(f"  Instrument: {fl['frequency_khz']} kHz, {fl['n_beams']} beam, "
                  f"{fl['beam_angle']}°, {fl['orientation']}-facing")
            print(f"  Coordinates: {fl['coord_transform']}")
            print(f"  Time: {adc['year'][0]}-{adc['month'][0]:02d}-{adc['day'][0]:02d} "
                  f"{adc['hour'][0]:02d}:{adc['minute'][0]:02d}:{adc['second'][0]:02d}"
                  f" → {adc['hour'][-1]:02d}:{adc['minute'][-1]:02d}:{adc['second'][-1]:02d}")
            print(f"  Heading:     {adc['heading'].min():.1f}° – {adc['heading'].max():.1f}°")
            print(f"  Pitch:       {adc['pitch'].min():.1f}° – {adc['pitch'].max():.1f}°")
            print(f"  Roll:        {adc['roll'].min():.1f}° – {adc['roll'].max():.1f}°")
            print(f"  Temperature: {adc['temperature'].min():.1f}° – {adc['temperature'].max():.1f}°C")
            print(f"  Depth:       {adc['depth'].min():.1f} – {adc['depth'].max():.1f} m")

            # Velocity stats (excluding -32768 bad data)
            vel = adc['velocity'].astype(np.float32)
            vel[vel == -32768] = np.nan
            valid_pct = np.count_nonzero(~np.isnan(vel)) / vel.size * 100
            print(f"  Velocity:    {valid_pct:.1f}% valid, "
                  f"range [{np.nanmin(vel):.0f}, {np.nanmax(vel):.0f}] mm/s")

            # Bottom track stats
            bt_range = adc['bt_range_cm'].astype(np.float32)
            bt_range[bt_range == 0] = np.nan
            print(f"  BT range:    {np.nanmin(bt_range)/100:.1f} – "
                  f"{np.nanmax(bt_range)/100:.1f} m "
                  f"({np.count_nonzero(~np.isnan(bt_range[:,0]))} valid)")

            if do_plot:
                out_png = path / (path.name + '_adcp.png')
                plot_adc(adc, gps=result.get('gps'), outpath=out_png)

    elif path.suffix.upper() == '.ADC':
        print(f"Parsing ADC: {path} ({os.path.getsize(path)/1e6:.1f} MB)")
        adc = parse_adc(path)
        fl = adc['fixed_leader']
        print(f"  {adc['n_ensembles']} ensembles, {adc['n_cells']} cells × "
              f"{fl['cell_size_cm']} cm, {fl['frequency_khz']} kHz {fl['orientation']}")
        if do_plot:
            # Try to find companion .GPS file
            gps = None
            gps_path = path.with_suffix('.GPS')
            if gps_path.exists():
                gps = parse_gps(gps_path)
                print(f"  GPS: {len(gps['lat'])} fixes loaded")
            out_png = path.with_suffix('.png')
            plot_adc(adc, gps=gps, outpath=out_png)

    elif path.suffix.upper() == '.GPS':
        gps = parse_gps(path)
        print(f"GPS: {len(gps['lat'])} fixes, "
              f"lat [{gps['lat'].min():.6f}, {gps['lat'].max():.6f}], "
              f"lon [{gps['lon'].min():.6f}, {gps['lon'].max():.6f}]")

    elif path.suffix.lower() == '.rmf':
        rmf = parse_rmf(path)
        print(f"RMF: {len(rmf['locations'])} locations, "
              f"{len(rmf['objectives'])} objectives")
        for loc in rmf['locations']:
            print(f"  Location: {loc.get('label', '?')} "
                  f"({loc.get('Type', loc.get('type', '?'))}) "
                  f"@ {loc.get('Position', '?')}")
        for i, obj in enumerate(rmf['objectives']):
            print(f"  Leg {i}: {obj.get('Type', '?')} → {obj.get('Destination', '?')}  "
                  f"depth={obj.get('Depth control', '?')}")

    elif path.suffix.lower() == '.txt':
        config = parse_adcp_config(path)
        print(f"ADCP Config: {len(config)} commands")
        for cmd, info in config.items():
            print(f"  {cmd:6s} = {info['value']:10s}  {info['comment']}")

    else:
        print(f"Unknown file type: {path.suffix}")
        sys.exit(1)
