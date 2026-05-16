# Tiny Matmul GPU — ISA Spec

Minimal Blackwell-style ISA for an fp8 matmul accelerator. Three data-movement / compute ops (LOAD, MMA, STORE) plus barrier ops for async completion.

## Memory Spaces

| Space | Purpose | Addressing |
|-------|---------|-----------|
| GMEM  | Off-chip DRAM. Holds A, B, D tensors. | Byte-addressed, 64-bit pointers. |
| SMEM  | Per-SM scratchpad. Holds tiles of A, B, and mbarrier objects. | Byte-addressed, 32-bit offsets from SMEM base. |
| TMEM  | Tensor memory. Holds MMA accumulator tiles only. | Tile-addressed (one address = one MMA-tile slot). |

No general-purpose register file. Operands are pointers into the spaces above, encoded as immediates in the instruction.

## Data Types

- **Operands (A, B)**: fp8 e4m3, stored in SMEM in MMA-native swizzled layout.
- **Accumulator (D)**: fp32, stored in TMEM.
- **Output (gmem)**: fp32 or fp8 — STORE selects via flag.

## Barrier State (mbarrier)

Each mbarrier is a 16-byte object living in SMEM. Layout:

```
offset  size  field         description
0       2     pending       arrivals remaining before flip-eligible
2       2     expected      reload value for `pending` after flip
4       4     tx_pending    bytes remaining before flip-eligible
8       1     phase         current phase (0 or 1); flips on completion
9       7     reserved
```

**Flip rule**: when `pending == 0` AND `tx_pending == 0`, hardware atomically:
1. toggles `phase` (0↔1)
2. reloads `pending = expected`
3. wakes any thread waiting on this barrier

Software is responsible for ensuring `expected` matches the number of arrivals it will issue in each phase.

## Async Issue Model

LOAD and MMA are **async**: they return immediately after issue. Completion is signaled by an arrival on the barrier supplied at issue time. Software tracks the *expected current phase* per barrier (starts at 0, flips on every successful WAIT).

STORE is **sync** in v1 (blocks until TMEM read drains and gmem write is in flight). May be promoted to async later if epilogue overlap matters.

## Instructions

### `BAR.INIT bar, count`
Initialize the mbarrier at SMEM offset `bar`.
- `expected ← count`
- `pending  ← count`
- `tx_pending ← 0`
- `phase ← 0`

Synchronous. Must be issued before any LOAD/MMA referencing `bar`.

### `LOAD bar, gmem_ptr, smem_ptr, bytes`
Async DMA from GMEM to SMEM.

**Issue-time (atomic with respect to the barrier):**
- `bar.tx_pending += bytes`

**On completion (all bytes written to SMEM):**
- `bar.tx_pending -= bytes`
- `bar.pending   -= 1`   ← one arrival per LOAD, regardless of size
- evaluate flip rule

Operands:
- `bar`: SMEM offset of the mbarrier (16-byte aligned).
- `gmem_ptr`: 64-bit GMEM address.
- `smem_ptr`: 32-bit SMEM offset (destination, alignment per layout requirement).
- `bytes`: transfer size, multiple of 16.

### `MMA bar, A_smem, B_smem, D_tmem, M, N, K, accum`
Async fp8 matmul: `D_tmem ← (accum ? D_tmem : 0) + A_smem @ B_smem^T`.

**On completion**:
- `bar.pending -= 1`
- evaluate flip rule

Operands:
- `bar`: SMEM offset of the mbarrier.
- `A_smem`, `B_smem`: SMEM offsets of operand tiles.
- `D_tmem`: TMEM tile address (accumulator slot).
- `M`, `N`, `K`: tile dimensions (constrained to the set the MMA unit natively supports, e.g. `{64,128,256} × {64,128,256} × {32,64}`; exact set TBD by datapath).
- `accum`: 1-bit flag. `0` = zero D before accumulating (first K-iter), `1` = accumulate into existing D.

### `STORE gmem_ptr, D_tmem, M, N, dtype`
Synchronous drain of a TMEM accumulator tile to GMEM.

- `dtype = 0`: write as fp32.
- `dtype = 1`: convert to fp8 e4m3 then write.

Operands as above. Blocks issuing thread until the TMEM read side completes; the GMEM write may continue in the memory system but is ordered before any subsequent STORE.

### `WAIT bar, phase`
Block until `bar.phase != phase`. Returns immediately if already flipped.

`phase` is the value the caller *believes* is current. Passing the wrong phase = either spurious immediate return or deadlock; this is a software contract, identical to Hopper/Blackwell semantics.

**Race-free pattern**: software keeps a 1-bit "expected phase" per barrier, initialized to `0`, toggled after every WAIT.

## Encoding (sketch, not final)

Fixed 64-bit instructions. 8-bit opcode + operand fields. Exact bit layout deferred until datapath is fixed.

| Opcode | Mnemonic |
|--------|----------|
| 0x00   | BAR.INIT |
| 0x01   | LOAD     |
| 0x02   | MMA      |
| 0x03   | STORE    |
| 0x04   | WAIT     |

## Canonical Matmul Kernel (single tile, K-loop unrolled to 2)

```
# barriers
BAR.INIT b_load, 2      # 2 LOADs per K-step (A + B)
BAR.INIT b_mma,  1      # 1 MMA per K-step

# K-step 0
LOAD b_load, A_gmem+0,  A_smem, A_bytes
LOAD b_load, B_gmem+0,  B_smem, B_bytes
WAIT b_load, 0
MMA  b_mma,  A_smem, B_smem, D_tmem, M, N, K, accum=0
WAIT b_mma,  0

# K-step 1
LOAD b_load, A_gmem+kA, A_smem, A_bytes
LOAD b_load, B_gmem+kB, B_smem, B_bytes
WAIT b_load, 1          # second phase
MMA  b_mma,  A_smem, B_smem, D_tmem, M, N, K, accum=1
WAIT b_mma,  1

# epilogue
STORE D_gmem, D_tmem, M, N, dtype=1
```

## Pipelined K-loop (load/MMA overlap, 2-stage SMEM buffer)

```
BAR.INIT b_load[0], 2
BAR.INIT b_load[1], 2
BAR.INIT b_mma,     1

# prime stage 0
LOAD b_load[0], A[0], smemA[0], ...
LOAD b_load[0], B[0], smemB[0], ...

for k in 0 .. K-1:
    stage      = k & 1
    next_stage = (k+1) & 1
    phase      = (k >> 1) & 1

    # issue next load early
    if k+1 < K:
        LOAD b_load[next_stage], A[k+1], smemA[next_stage], ...
        LOAD b_load[next_stage], B[k+1], smemB[next_stage], ...

    WAIT b_load[stage], phase
    MMA  b_mma, smemA[stage], smemB[stage], D_tmem, M, N, K, accum=(k != 0)
    WAIT b_mma, phase_of(k)

STORE D_gmem, D_tmem, M, N, dtype=1
```

## Open Questions

- **MMA tile sizes**: which `(M,N,K)` triples does the datapath natively support? Drives operand-layout requirements in SMEM.
- **TMEM slot count**: how many accumulator tiles per SM? Determines max in-flight MMAs.
- **STORE epilogue**: keep sync, or add a barrier-arrival form for overlap with the next kernel's LOADs?
- **Scaling**: fp8 typically needs per-tensor or per-tile scale. Bake into MMA (extra operand), into STORE, or punt to host?
- **SMEM↔TMEM moves**: needed if we ever want to spill/reload accumulators, or do epilogue fusion. Skipped for v1.
