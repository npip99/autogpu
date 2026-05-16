"""
Phase 3 milestone — pymodel end-to-end matmul.

When this passes, the architecture is proven: every subsequent failure
in Phase 4/5 is an RTL-vs-pymodel disagreement, not an architectural question.
"""

import numpy as np

from config import MMA_K, MMA_M, MMA_N, SMEM_TILE_BASE
from golden.matmul_reference import generate
from pymodel.cmdproc import BAR_INIT, LOAD, MMA, STORE, WAIT
from pymodel.sim import Sim


def test_single_tile_matmul_random():
    """Random A,B fp8 → C fp32, end-to-end through the full pymodel."""
    A_bytes, B_bytes, C_expected = generate(MMA_M, MMA_N, MMA_K, seed=0)

    A_gmem = 0
    B_gmem = len(A_bytes)
    C_gmem = 16 * 1024  # well past A/B
    A_smem = SMEM_TILE_BASE
    B_smem = SMEM_TILE_BASE + len(A_bytes)

    sim = Sim()
    sim.load_gmem(A_gmem, A_bytes)
    sim.load_gmem(B_gmem, B_bytes)

    program = [
        BAR_INIT(bar=0, count=2),  # 2 LOAD arrivals
        BAR_INIT(bar=1, count=1),  # 1 MMA arrival
        LOAD(bar=0, gmem_ptr=A_gmem, smem_ptr=A_smem, bytes_n=len(A_bytes)),
        LOAD(bar=0, gmem_ptr=B_gmem, smem_ptr=B_smem, bytes_n=len(B_bytes)),
        WAIT(bar=0, expected_phase=0),
        MMA(bar=1, a_smem_offset=A_smem, b_smem_offset=B_smem,
            d_tmem_slot=0, accum=0),
        WAIT(bar=1, expected_phase=0),
        STORE(tmem_slot=0, gmem_ptr=C_gmem, dtype=0),  # fp32 output
    ]
    sim.load_program(program)
    cycles = sim.run_until_idle()

    C_bytes = sim.read_gmem(C_gmem, MMA_M * MMA_N * 4)
    C_actual = np.frombuffer(C_bytes, dtype="<f4").reshape(MMA_M, MMA_N)
    np.testing.assert_allclose(C_actual, C_expected, rtol=0, atol=1e-5)
    print(f"\ne2e matmul completed in {cycles} cycles")
