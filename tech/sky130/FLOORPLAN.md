# Chip-top floorplan (sky130)

Canonical reference for placing modules at chip-top. Every submodule harden
should derive its `DIE_AREA`, `FP_PIN_ORDER_CFG`, and PDN constraints from
this doc, not from `runs/`-data alone.

For per-config-knob lessons (met5 PDN-only, sizing math, macro PDN
alignment), see [submodules/README.md](submodules/README.md#sizing--config-tips).

---

## 1. Adjacency map

Whoever is next to whom, and why. ASCII coordinates are µm in the chip's
absolute frame (origin SW corner).

```
                      ┌─────────────────────────────────────────────────────────┐
                      │  cmdproc            barrier                 reset_seq   │   ← north strip
                      │  (~1500 × 1500)     (~660 × 660)            (~74 × 74)   │     ~1500 µm tall
                      │  IMEM stays north   debug-bus output                     │
                      ├─────────────────────────────────────────────────────────┤
                      │ ┌───────────┐  ┌──────────────────────────┐  ┌────────┐ │
                      │ │ SMEM 16-W │  │                          │  │SMEM 16-│ │
                      │ │  banks    │  │     compute_array        │  │  E     │ │
                      │ │ 16 SRAMs  │  │ 32 × 32 mac_tmem_cell    │  │banks   │ │
                      │ │ ~700 ×    │  │  macros @ 614.4 × 612.72 │  │~700 ×  │ │
                      │ │ 19,500    │  │  pitch  =  ~19,700 ×     │  │19,500  │ │
                      │ │           │  │           ~19,600 µm     │  │        │ │
                      │ ├───────────┤  │                          │  ├────────┤ │
                      │ │   LOAD    │  │                          │  │        │ │
                      │ │ ~700×700  │  │                          │  │        │ │
                      │ └───────────┘  └──────────────────────────┘  └────────┘ │
                      │                ┌──────────────────────────┐             │
                      │                │           STORE          │             │
                      │                │       ~1500 × 1500       │             │
                      │                └──────────────────────────┘             │
                      └─────────────────────────────────────────────────────────┘
                            ↓ GMEM rd pads (west bottom)          ↓ GMEM wr pads (south)
```

**Hard adjacency constraints** (failure to honor causes routing blowups):

| Constraint | Reason | Wire impact |
|---|---|---|
| STORE south of compute_array, ≤ 200 µm gap | 1024-b `drain_row_data` bus | ~200 µm wires, 1024 wide |
| SMEM west of compute_array | 256-b `rd_a_data` bus | ~50 µm, 256 wide |
| SMEM east of compute_array | 256-b `rd_b_data` bus | ~50 µm, 256 wide |
| LOAD west, abuts west SMEM | 128-b `smem_wr_data` from LOAD into SMEM | ~50 µm, 128 wide |
| LOAD adjacent to chip's `mc_rd_*` pads | 128-b `gmem_rd_data` from off-chip | depends on pad position |
| STORE adjacent to chip's `mc_wr_*` pads | 128-b `mc_wr_data` to off-chip | depends on pad position |
| cmdproc north (control bus distribution) | 32-b control nets to each engine | longer is OK; control is registered |

---

## 2. Bus inventory (cross-module signals ≥32 bits)

Every wide bus listed by source → sink, width, criticality.

| Bus | Width | Source | Sink | Hard-adjacency req? | Notes |
|---|---|---|---|---|---|
| `drain_row_data` | 1024 b | compute_array | STORE | YES (south) | one row per cycle; 8 cycles to drain one tile to GMEM |
| `rd_a_data` | 256 b | SMEM west | compute_array | YES (west) | one operand row per cycle |
| `rd_b_data` | 256 b | SMEM east | compute_array | YES (east) | one operand column per cycle |
| `gmem_rd_data` | 128 b | chip pad / mc_rd | LOAD | YES (chip edge) | BEAT_BYTES=16 → 128 b |
| `smem_wr_data` | 128 b | LOAD | SMEM | YES (LOAD↔SMEM-west) | matches GMEM read width |
| `mc_wr_data` | 128 b | STORE | chip pad / mc_wr | YES (chip edge) | matches BEAT |
| `cp_load_g/s/b/bar` | 4×32 = 128 b | cmdproc | LOAD | no (registered, slow path) | issue path |
| `cp_mma_*` | ~7×32 = 224 b | cmdproc | compute_array | no (registered) | issue path |
| `cp_store_*` | ~3×32 = 96 b | cmdproc | STORE | no (registered) | issue path |
| `barrier inputs` | 4×32 + 5×32 = 288 b | cmdproc + LOAD + compute_array | barrier | no (registered) | mostly aggregated counter updates |

**Rule of thumb**: any bus ≥256 bits MUST have its endpoints abut, because
routing 256 parallel wires across mm of die wastes routing layers and
introduces serpentine paths. ≤128 bits is forgiving — chip-top routing
absorbs it.

---

## 3. Per-module slot specs

Sizes from successful hardens (in `runs/.../final/metrics.json`) or
predicted from cell count + 50% util target.

| Module | Status | Die (µm) | Util | Cells | Macro children | Pin-order TBD |
|---|---|---|---|---|---|---|
| `reset_seq` | ✓ hardened | 74 × 74 | 31% | 157 | none | low priority (13 pins) |
| `barrier` | ✓ hardened | 656 × 656 | 15% | 13,862 | none | TODO |
| `load` | ✓ hardened | **700 × 700** | 44% | 20,512 | none | TODO (see §4 below) |
| `mac_tmem_cell` | ✓ hardened (Phase 7i-2b) | 436 × 440 | 45% | 14,786 | fp8_decode + fp32_fma | TODO — affects compute_array PDN |
| `mac_array_small` | ✓ hardened | 2,600 × 2,600 | — | (16 macro) | mac_tmem_cell × 16 | proof-of-concept |
| `tile_buf_8row` | ✓ hardened, **bad pin layout** | 1,316 × 1,326 | 33% | 87,963 | none | YES — re-harden with pin order before store integration |
| `store` | running (monolithic) | 1500 × 1500 (current attempt) | — | (estimate 50k–80k) | none | YES |
| `compute_array` | running (Phase 7i-6 systolic) | predicted ~19,700 × 19,600 | — | 1024 × mac_tmem_cell + thin glue | mac_tmem_cell × 1024 | YES (large) |
| `cmdproc` | not started | predicted ~1500 × 1500 | — | imem (16k FFs) + FSM | imem could be SRAM later | TODO |
| `smem` | yosys block (mem2reg) | predicted ~700 × 19,500 (column) | — | (32 SRAM macros + arbiter) | sky130_sram_1kbyte × 32 | TODO |
| `chip_top` | not attempted past Phase 7g | est. ~22,000 × 23,000 µm | — | sum of above + glue | all submodules above | composed at top |

**Estimated total chip die**: ~22 × 23 mm = ~506 mm². This **exceeds the
sky130 reticle limit (~100 mm²)** for actual fabrication. This is a toy /
educational design; expect to either (a) live with it being simulation-only,
(b) scale down MMA dimensions (e.g. 16×16 instead of 32×32) for a fabable
toy, or (c) target a denser PDK. **Decide before final integration.**

---

## 4. Per-module pin-order configs (the "wires-go-the-right-way" rule)

Each submodule's IO pins should land on the edge facing the chip-top neighbor
that consumes them. OpenLane's default IO placer spreads pins randomly around
the perimeter; without an explicit pin-order config, chip-top routing makes
long detours. Use `FP_PIN_ORDER_CFG` per module.

### `load`

```
# tech/sky130/submodules/load/load.pin_order.cfg
#N: cmdproc connections (cmdproc sits north of LOAD)
issue_en
gmem_ptr\[.*\]
smem_ptr\[.*\]
bytes_n\[.*\]
bar_id\[.*\]
busy
done
accept

#E: SMEM connections (LOAD west, SMEM-west to its east)
smem_wr_en
smem_wr_addr\[.*\]
smem_wr_data\[.*\]
smem_wr_stall_in

#W: GMEM (chip's west-bottom pads)
gmem_rd_en
gmem_rd_addr\[.*\]
gmem_rd_data\[.*\]
gmem_rd_valid

#S: barrier (barrier sits north, but we route the slow bus south to free
#   the cmdproc + GMEM bandwidth on N/W)
add_tx_en
add_tx_bar_id\[.*\]
add_tx_bytes\[.*\]
sub_tx_en
sub_tx_bar_id\[.*\]
sub_tx_bytes\[.*\]
arrive_en
arrive_bar_id\[.*\]
```

### `store`

```
# tech/sky130/submodules/store/store.pin_order.cfg
#N: compute_array drain interface (compute_array sits north of STORE)
drain_row_data\[.*\]    # 1024 b
drain_row_valid
drain_row_idx\[.*\]
drain_last
drain_done
drain_issue
drain_slot\[.*\]

#W: cmdproc (cmdproc north-west)
issue_en
gmem_ptr\[.*\]
tmem_slot\[.*\]
dtype
busy
done

#S: chip's mc_wr_* pads
mc_wr_en
mc_wr_addr\[.*\]
mc_wr_data\[.*\]    # 128 b
```

### `tile_buf_8row` (re-harden needed)

```
# tile_buf_8row.pin_order.cfg
#S: write-side (input from compute_array's drain, via store)
wr_en
wr_row\[.*\]
wr_data\[.*\]    # 1024 b

#N: read-side (out to store's pack/encode logic)
rd_en
rd_row\[.*\]
rd_data\[.*\]    # 1024 b

#W: clk + reset
clk
reset
```

### `compute_array`

```
# tech/sky130/submodules/compute_array/compute_array.pin_order.cfg
#W: SMEM-west operand A
rd_a_en
rd_a_addr\[.*\]
rd_a_data\[.*\]    # 256 b
rd_a_valid
rd_a_stall_in

#E: SMEM-east operand B
rd_b_en
rd_b_addr\[.*\]
rd_b_data\[.*\]    # 256 b
rd_b_valid
rd_b_stall_in

#N: cmdproc issue + barrier arrival
mma_issue
mma_slot\[.*\]
mma_accum
mma_bar_id\[.*\]
issue_a_off\[.*\]
issue_b_off\[.*\]
issue_a_stride\[.*\]
issue_b_stride\[.*\]
mma_busy
mma_done
arrive_en
arrive_bar_id\[.*\]
scrub_en

#S: STORE drain interface
drain_issue
drain_slot\[.*\]
drain_busy
drain_done
drain_row_valid
drain_row_data\[.*\]    # 1024 b
drain_row_idx\[.*\]
drain_last
```

### `barrier`

```
# barrier.pin_order.cfg
#S: query results to cmdproc (1 b)
wait_done

#N: query inputs (all from cmdproc + LOAD)
query_bar_id\[.*\]
query_expected_phase
init_en
init_bar_id\[.*\]
init_count\[.*\]

#E: tx-add/sub/arrive aggregated from LOAD + compute_array
add_tx_en
add_tx_bar_id\[.*\]
add_tx_bytes\[.*\]
sub_tx_en
sub_tx_bar_id\[.*\]
sub_tx_bytes\[.*\]
arrive_en_a
arrive_bar_id_a\[.*\]
arrive_en_b
arrive_bar_id_b\[.*\]

#W: observable state debug bus (chip-edge if needed for testing)
bars_pending\[.*\]
bars_expected\[.*\]
bars_tx_pending\[.*\]
bars_phase\[.*\]
```

### `cmdproc`

```
# cmdproc.pin_order.cfg
#N: external instruction pad (push_en, push_instr — chip-edge input)
push_en
push_instr\[.*\]    # 256 b

#S: control busses to all engines (cmdproc north of everything)
cp_load_*
cp_mma_*
cp_store_*
cp_bar_*

#E: barrier query channel
barrier_query_*
barrier_wait_done
```

### `smem`

```
# smem.pin_order.cfg
#N: LOAD write bus (LOAD sits to LOAD-SMEM-west's east)
wr_en
wr_addr\[.*\]
wr_data\[.*\]    # 128 b

#E: compute_array read (rd_a only for west-SMEM; rd_b only for east-SMEM)
rd_a_en / rd_b_en
rd_a_addr\[.*\] / rd_b_addr\[.*\]
rd_a_data\[.*\] / rd_b_data\[.*\]    # 256 b
rd_*_valid
rd_*_stall

#S: reset_seq scrub
scrub_en
scrub_addr\[.*\]
```

---

## 4.1 Bit-order alignment for abutting buses

**Pin order isn't just `which edge` — it's `bit position along the edge`**,
and both ends of a cross-module bus must agree. Otherwise the chip-top
router has to permute 256–1024 wires at the boundary, costing routing
layers and creating congestion.

For an abutting bus (one module's south meets another's north, sharing
the same X axis), list bits in **identical order** in both `.pin_order.cfg`
files. OpenLane usually expands `bus\[.*\]` in numerical order, but for
hard-constraint buses it's safer to be explicit:

```
# compute_array south face (drain_row_data goes OUT, low bits first):
drain_row_data\[0\]
drain_row_data\[1\]
...
drain_row_data\[1023\]

# store north face (drain_row_data comes IN, low bits first — SAME order):
drain_row_data\[0\]
drain_row_data\[1\]
...
drain_row_data\[1023\]
```

Abutting bus pairs in this design:

| Bus | Wide-side ↑ | Wide-side ↓ | Width |
|---|---|---|---|
| `drain_row_data` | compute_array south | STORE north | 1024 b |
| `rd_a_data` | SMEM-west east | compute_array west | 256 b |
| `rd_b_data` | SMEM-east west | compute_array east | 256 b |
| `smem_wr_data` | LOAD east | SMEM-west west | 128 b |
| `gmem_rd_data` | chip pad / mc_rd | LOAD west | 128 b |
| `mc_wr_data` | STORE south | chip pad | 128 b |

For perpendicular abutting (e.g. LOAD east meets SMEM-west west, but
LOAD is shorter than SMEM along Y), the bus pins should still be at
matching positions in the overlap region. OpenLane can't enforce that
across modules — chip-top placement must align module origins so the
overlap is non-zero and the pin positions match.

---

## 5. Module-hardening process going forward

Every new harden (or re-harden) follows this checklist:

1. **Look up its slot in §3 of this doc** — die size + neighbors.
2. **Look up its pin-order config in §4** — copy the relevant section into
   a `<module>.pin_order.cfg` file in the submodule dir.
3. **Add to `config.yaml`**:
   ```yaml
   FP_PIN_ORDER_CFG: dir::<module>.pin_order.cfg
   ```
4. **Honor the sizing & PDN rules in
   [submodules/README.md](submodules/README.md#sizing--config-tips)**:
   - `FP_IO_HLAYER: met3` (no met5)
   - `RT_MAX_LAYER: met4`
   - Die size proportional to actual cell area
5. **For modules with hardened macro children** (compute_array, smem,
   eventually store after tile_buf_8row integration), also honor PDN-strap
   alignment: `origin = (−4.72 + k·153.6, −10.08 + k·153.18)`, pitch =
   multiple of `153.6 × 153.18`.

---

## 6. Open questions / TODO

- [ ] **Confirm chip-top reticle target**: 506 mm² won't fab on sky130.
      Need a decision on scaling down (e.g. 16×16 MMA → ~5 mm² chip) or
      accepting "simulation-only" status.
- [ ] **Refactor `store` to use `tile_buf_8row`**: bigger refactor; needed
      to make store fast to re-harden.
- [ ] **smem yosys mem2reg blocker**: smem standalone synth fails at yosys
      stage 02 with the latch-inference error from earlier in the project.
      Needs the `mem2reg` workaround that was applied in `compute_array.sv`
      for the drain pack loop.
- [ ] **Phase 7i-3 / 7i-6 systolic compute_array harden**: blocked on
      compute_array's current synth completing. Once done, this becomes
      our first really-big macro and exposes the M=N=32 PDN-alignment
      math under real conditions.
- [ ] **Pin-order configs for all modules**: §4 has the recipes; need to
      write the actual `.cfg` files and add to each `config.yaml`. Best
      done in the order the modules need to be re-hardened
      (tile_buf_8row first, since it's the most-broken).
