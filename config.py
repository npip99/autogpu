"""Canonical configuration shared by Python pymodel and (later) Verilog RTL.

To change the MMA tile size, edit MMA_M/MMA_N/MMA_K here and re-run tests.
The pymodel reads this directly; the RTL build step (Phase 4) will emit a
matching `config.svh` with `define directives from these values.
"""

# --- MMA native shape ---
# The MAC grid is MMA_M x MMA_N cells. Each tile takes MMA_K cycles to compute.
# fp8 inputs, fp32 accumulator.
MMA_M = 32
MMA_N = 32
MMA_K = 32

# --- On-chip memory sizes ---
SMEM_BYTES = 8 * 1024           # scratchpad: barriers + operand tiles.
                                # Post-B1: 2 regions × 8 banks × 128 words × 4 B
                                # = 8 KB. Region 0 (addr 0..4095) is A operand,
                                # region 1 (addr 4096..8191) is B operand. Each
                                # bank is one fakeram7_256x32 (depth 128 used).
TMEM_SLOTS = 4                  # number of MMA_M x MMA_N fp32 accumulator tiles
GMEM_BYTES = 1 << 24            # 16 MB DRAM model (TB-side only)

# --- Datapath widths ---
BEAT_BYTES = 16                 # bytes per cycle on GMEM and SMEM data ports
                                # (one "beat" = one memory transaction unit)

# --- Barriers ---
NUM_BARRIERS = 8                # mbarrier slots, reserved at start of SMEM
BARRIER_BYTES = 16              # each mbarrier object is 16 bytes

# --- Instruction encoding ---
INSTR_BYTES = 8                 # 64-bit fixed-width instructions
INSTR_FIFO_DEPTH = 64           # legacy alias: cmdproc.imem capacity (max asm
                                # program length). Kept for back-compat with
                                # tests that read this. Use IMEM_DEPTH below.
IMEM_DEPTH = 64                 # max asm program length (cmdproc.imem)
LOAD_FIFO_DEPTH = 8             # pending LOAD command queue depth (load engine).
                                # Each +1 entry costs ~7*32 flops in load.sv.
                                # Worst-case observed test burst is 2 LOADs;
                                # 8 gives 4× margin and shrinks load yosys synth
                                # ~8× vs the old 64.

# --- Derived (do not edit) ---
TMEM_BYTES = TMEM_SLOTS * MMA_M * MMA_N * 4
SMEM_BARRIER_REGION_BYTES = NUM_BARRIERS * BARRIER_BYTES
SMEM_TILE_BASE = SMEM_BARRIER_REGION_BYTES  # tiles start after barrier region
A_TILE_BYTES = MMA_M * MMA_K                # fp8, 1 byte per element
B_TILE_BYTES = MMA_K * MMA_N                # fp8
D_TILE_BYTES = MMA_M * MMA_N * 4            # fp32 accumulator tile

# --- Opcodes (8-bit) ---
OP_BAR_INIT = 0x00
OP_LOAD     = 0x01
OP_MMA      = 0x02
OP_STORE    = 0x03
OP_WAIT     = 0x04
OP_REPEAT   = 0x05   # REPEAT N: begin counted loop, body runs N times
OP_END      = 0x06   # END: close innermost REPEAT; jump back if more iters
