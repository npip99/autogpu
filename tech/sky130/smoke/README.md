# OpenLane sky130 toolchain smoke test

Validates the OpenLane → sky130 → GDS toolchain on a 30-line design before
spending time iterating on full-chip synthesis. If this passes, the install
is correct; any failure on `chip_top` is about `chip_top`'s RTL or config, not
the toolchain.

## What's here

| File | Purpose |
|---|---|
| `smoke_top.sv` | Minimal RTL — 8-bit shift-XOR register. Pure Verilog-2005-style SV (no packages, no automatic functions, no part-select-on-call). |
| `config.yaml` | OpenLane 2 config: 25 MHz target, 100 µm × 100 µm die. |
| `run.sh` | Driver — activates the venv, invokes `python -m openlane --dockerized`. |

## One-time setup

1. Install Docker; add your user to the `docker` group (or run via `sg docker -c`).
2. From repo root: `uv sync --extra synth`
3. Pull the OpenLane image: `docker pull ghcr.io/efabless/openlane2:2.3.10`

## Run it

```bash
./run.sh
# or, if you haven't logged out since adding yourself to the docker group:
sg docker -c ./run.sh
```

Expected output on success: 78 stages complete in ~40 seconds, all of
`Antenna`, `LVS`, `DRC` pass, GDS at
`runs/<timestamp>/final/gds/smoke_top.gds`.

## If it fails

Try in this order — each diagnoses a different layer of the stack:

1. **`docker run --rm ghcr.io/efabless/openlane2:2.3.10 yosys -V`** —
   Confirms Docker + the OpenLane image. Should print a Yosys version.
2. **`python -m openlane --version`** — Confirms the Python orchestrator
   is installed. Should print `OpenLane v2.3.10`.
3. **Re-run `./run.sh`** and read `runs/<timestamp>/<step>/` logs for the
   failing step.

Common breakages:

- `the input device is not a TTY` — `--docker-no-tty` must come **before**
  `--dockerized` in the openlane CLI invocation. Already correct in `run.sh`.
- `OPENLANE_NO_TTY` env var: not needed if you use the flag.
- libparse build error during `uv sync` — the project pins Python 3.12
  precisely because libparse only ships cp312 wheels. If you bypassed the
  pin, you'll hit the libparse sdist's broken `make patch` step.
