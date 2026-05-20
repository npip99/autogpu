#!/usr/bin/env python3
"""
Generate a stub Liberty (.lib) file for a module — port declarations
only, no timing arcs. Sufficient for chip_top's STA to elaborate (it'll
report "no constraints" everywhere, which is fine for stub-mode trial
hardens — we're not validating timing, just routing topology).

Usage:
    gen_stub_lib.py <module_name> <repo_root>

Reads the module's SV port list (same parser as gen_stub_lef.py).
Writes <repo_root>/build/sv2v/lib-stub/<module>.lib.

The library cell is named exactly after the module so chip_top's
`MACROS:` entry resolves correctly. The "stub_" prefix on the library
name is just to make it obvious in error messages that timing here is
fictional.
"""

import sys
from pathlib import Path

# Reuse the SV parser.
sys.path.insert(0, str(Path(__file__).parent))
from gen_stub_lef import parse_sv_ports  # noqa: E402


# Conservative defaults so synthesis tools don't reject the file.
DEFAULT_CAP = 0.01   # pF per input pin


def emit_lib(module: str, ports: dict[str, dict]) -> str:
    lines = [
        f'library (stub_{module}) {{',
        '  technology (cmos);',
        '  delay_model : table_lookup;',
        '  time_unit : "1ns";',
        '  voltage_unit : "1V";',
        '  current_unit : "1mA";',
        '  resistance_unit : "1kohm";',
        '  capacitive_load_unit (1, pf);',
        '  pulling_resistance_unit : "1kohm";',
        '  leakage_power_unit : "1uW";',
        '  nom_voltage : 1.8;',
        '  nom_temperature : 25;',
        '  nom_process : 1;',
        '  default_inout_pin_cap : 0.01;',
        '  default_input_pin_cap : 0.01;',
        '  default_output_pin_cap : 0.0;',
        '  default_max_transition : 1.0;',
        '  default_cell_leakage_power : 0.0;',
        '  default_fanout_load : 1.0;',
        '  default_max_fanout : 100.0;',
        '  default_wire_load_capacitance : 0.0;',
        '  default_wire_load_resistance : 0.0;',
        '  default_wire_load_area : 0.0;',
        # OpenROAD requires explicit threshold percentages.
        '  input_threshold_pct_rise : 50;',
        '  input_threshold_pct_fall : 50;',
        '  output_threshold_pct_rise : 50;',
        '  output_threshold_pct_fall : 50;',
        '  slew_lower_threshold_pct_rise : 20;',
        '  slew_lower_threshold_pct_fall : 20;',
        '  slew_upper_threshold_pct_rise : 80;',
        '  slew_upper_threshold_pct_fall : 80;',
        '  slew_derate_from_library : 1;',
        f'  cell ({module}) {{',
        '    interface_timing : true;',
        '    area : 1.0;',
    ]
    # Sort ports for stable output across runs.
    for name in sorted(ports.keys()):
        port = ports[name]
        direction = port["direction"].lower()
        width = port["width"]
        if width == 1:
            lines += [
                f'    pin ({name}) {{',
                f'      direction : {direction};',
                f'      capacitance : {DEFAULT_CAP};',
                '    }',
            ]
        else:
            # Emit a bus declaration.
            lines += [
                f'    bus ({name}) {{',
                '      bus_type : ' + f'"bus{width}"' + ';',
                f'      direction : {direction};',
                f'      capacitance : {DEFAULT_CAP};',
                '    }',
            ]
    lines += [
        '  }',
        '}',
    ]
    # Emit the bus type declarations at the top — Liberty syntax requires
    # them before the library block. Collect distinct widths.
    bus_widths = sorted({p["width"] for p in ports.values() if p["width"] > 1})
    if bus_widths:
        prelude = []
        for w in bus_widths:
            prelude += [
                f'  type (bus{w}) {{',
                '    base_type : array;',
                '    data_type : bit;',
                f'    bit_width : {w};',
                f'    bit_from : {w - 1};',
                '    bit_to : 0;',
                '    downto : true;',
                '  }',
            ]
        # Insert after the `library (... )` header (right after first line).
        lines = lines[:1] + prelude + lines[1:]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.exit(f"usage: {argv[0]} <module_name> <repo_root>")
    module = argv[1]
    repo = Path(argv[2]).resolve()
    sv_path = repo / module / f"{module}.sv"
    out_path = repo / "build/sv2v/lib-stub" / f"{module}.lib"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ports = parse_sv_ports(sv_path, module)
    if not ports:
        sys.exit(f"ERROR: no ports parsed from {sv_path}")
    out_path.write_text(emit_lib(module, ports))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
