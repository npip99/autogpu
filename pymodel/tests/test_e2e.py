"""
Phase 3 milestone test: random A, B → C, end-to-end through the full pymodel.

When this passes, the architecture is proven. Every subsequent failure in
Phase 4/5 is an RTL-vs-pymodel disagreement, never an architecture question.
"""

# from pymodel.sim import Sim
# from pymodel.cmdproc import Instr  # or whatever the instr type is named
# from golden.matmul_reference import generate
# from config import MMA_M, MMA_N, MMA_K, SMEM_TILE_BASE
# import numpy as np


def test_single_tile_matmul_random():
    """The headline test.

    1. golden.matmul_reference.generate(MMA_M, MMA_N, MMA_K, seed=0)
       → (A_bytes, B_bytes, C_expected fp32)
    2. Place A_bytes at gmem offset 0; B_bytes at gmem offset len(A_bytes);
       reserve C output region after that.
    3. Assemble the 7-instruction kernel:
         BAR_INIT(b=0, count=2)
         BAR_INIT(b=1, count=1)
         LOAD(bar=0, gmem=A_addr, smem=A_smem, bytes=len(A_bytes))
         LOAD(bar=0, gmem=B_addr, smem=B_smem, bytes=len(B_bytes))
         WAIT(bar=0, phase=0)
         MMA(bar=1, A_smem, B_smem, D_tmem=0, accum=0)
         WAIT(bar=1, phase=0)
         STORE(tmem=0, gmem=C_addr, dtype=0)  # fp32 output
    4. sim.load_program(program); sim.run_until_idle()
    5. C_bytes = sim.read_gmem(C_addr, MMA_M*MMA_N*4)
    6. C_actual = np.frombuffer(C_bytes, dtype=np.float32).reshape(MMA_M, MMA_N)
    7. np.testing.assert_allclose(C_actual, C_expected, rtol=0, atol=0)
       (Tolerance 0 because both sides go through the same decode→fp32 path.)
    """
    raise NotImplementedError
