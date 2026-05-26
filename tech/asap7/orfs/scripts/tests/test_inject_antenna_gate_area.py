"""Tests for inject_antenna_gate_area.patch_lef.

Run directly:
    pytest tech/asap7/orfs/scripts/tests/test_inject_antenna_gate_area.py

(Not picked up by the repo's default `pytest` invocation, which is scoped to
pymodel/tests via pyproject.toml.)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from inject_antenna_gate_area import GATE_AREA_UM2, patch_lef  # noqa: E402


# --- fixtures --------------------------------------------------------------

# A two-pin macro: one INPUT (should get ANTENNAGATEAREA), one OUTPUT
# (must remain untouched).
STDCELL_LEF = """\
VERSION 5.8 ;

MACRO INV_X1
  CLASS CORE ;
  PIN A
    DIRECTION INPUT ;
    USE SIGNAL ;
    PORT
      LAYER M1 ;
        RECT 0.0 0.0 0.1 0.1 ;
    END
  END A
  PIN Y
    DIRECTION OUTPUT ;
    USE SIGNAL ;
    PORT
      LAYER M1 ;
        RECT 0.2 0.0 0.3 0.1 ;
    END
  END Y
END INV_X1

END LIBRARY
"""

# Same macro but pin A already has ANTENNAGATEAREA — patcher must leave it.
STDCELL_LEF_PREEXISTING = """\
VERSION 5.8 ;

MACRO INV_X1
  CLASS CORE ;
  PIN A
    DIRECTION INPUT ;
    USE SIGNAL ;
    ANTENNAGATEAREA 0.123 ;
    PORT
      LAYER M1 ;
        RECT 0.0 0.0 0.1 0.1 ;
    END
  END A
END INV_X1

END LIBRARY
"""

# Pin with no PORT block — patcher should still inject (before END).
STDCELL_LEF_NO_PORT = """\
VERSION 5.8 ;

MACRO STUB
  CLASS CORE ;
  PIN A
    DIRECTION INPUT ;
    USE SIGNAL ;
  END A
END STUB

END LIBRARY
"""


def _write(tmp: Path, name: str, contents: str) -> Path:
    p = tmp / name
    p.write_text(contents)
    return p


# The patcher prepends a banner that itself mentions "ANTENNAGATEAREA"
# (in human-readable prose). Strip it before counting occurrences in
# the LEF body so we measure injections, not banner text.
_BANNER_END = "# =============================================\n"


def _strip_banner(body: str) -> str:
    if body.startswith("# === PATCHED by inject_antenna_gate_area.py ==="):
        idx = body.find(_BANNER_END)
        assert idx != -1, "patcher produced a banner without its terminator"
        return body[idx + len(_BANNER_END):]
    return body


# --- tests -----------------------------------------------------------------

def test_injects_into_input_pin(tmp_path: Path):
    src = _write(tmp_path, "src.lef", STDCELL_LEF)
    dst = tmp_path / "dst.lef"

    patched, already = patch_lef(src, dst)

    assert patched == 1
    assert already == 0
    body = dst.read_text()

    # Banner is prepended.
    assert body.startswith("# === PATCHED by inject_antenna_gate_area.py ===")
    # Gate-area line is present with the exact configured value.
    assert f"ANTENNAGATEAREA {GATE_AREA_UM2} ;" in body
    # OUTPUT pin Y was not touched.
    y_block = body.split("PIN Y", 1)[1].split("END Y", 1)[0]
    assert "ANTENNAGATEAREA" not in y_block


def test_injects_before_port_inside_input_pin(tmp_path: Path):
    src = _write(tmp_path, "src.lef", STDCELL_LEF)
    dst = tmp_path / "dst.lef"
    patch_lef(src, dst)
    body = dst.read_text()

    # Inside PIN A, ANTENNAGATEAREA must appear before the first PORT
    # (LEF spec). Slice the pin A body and check ordering.
    a_block = body.split("PIN A", 1)[1].split("END A", 1)[0]
    assert "ANTENNAGATEAREA" in a_block
    assert "PORT" in a_block
    assert a_block.index("ANTENNAGATEAREA") < a_block.index("PORT")


def test_idempotent_on_already_patched_input(tmp_path: Path):
    src = _write(tmp_path, "src.lef", STDCELL_LEF_PREEXISTING)
    dst = tmp_path / "dst.lef"

    patched, already = patch_lef(src, dst)

    assert patched == 0
    assert already == 1
    lef_body = _strip_banner(dst.read_text())
    # The pre-existing value must not have been duplicated or replaced.
    assert lef_body.count("ANTENNAGATEAREA") == 1
    assert "ANTENNAGATEAREA 0.123 ;" in lef_body


def test_idempotent_on_double_patch(tmp_path: Path):
    src = _write(tmp_path, "src.lef", STDCELL_LEF)
    dst1 = tmp_path / "dst1.lef"
    dst2 = tmp_path / "dst2.lef"

    patch_lef(src, dst1)
    patched2, already2 = patch_lef(dst1, dst2)

    # The second pass sees the gate area already present.
    assert patched2 == 0
    assert already2 == 1
    body = dst2.read_text()
    # Banner appears only once (no doubling).
    assert body.count("# === PATCHED by inject_antenna_gate_area.py ===") == 1
    # Gate-area line appears exactly once in the LEF body (banner mentions
    # ANTENNAGATEAREA in prose, so count after stripping it).
    assert _strip_banner(body).count("ANTENNAGATEAREA") == 1


def test_deterministic(tmp_path: Path):
    src = _write(tmp_path, "src.lef", STDCELL_LEF)
    dst1 = tmp_path / "dst1.lef"
    dst2 = tmp_path / "dst2.lef"

    patch_lef(src, dst1)
    patch_lef(src, dst2)

    assert dst1.read_bytes() == dst2.read_bytes()


def test_injects_before_end_when_no_port(tmp_path: Path):
    src = _write(tmp_path, "src.lef", STDCELL_LEF_NO_PORT)
    dst = tmp_path / "dst.lef"

    patched, already = patch_lef(src, dst)

    assert patched == 1
    assert already == 0
    body = dst.read_text()
    a_block = body.split("PIN A", 1)[1].split("END A", 1)[0]
    assert "ANTENNAGATEAREA" in a_block


def test_unterminated_pin_raises(tmp_path: Path):
    bad = "MACRO X\n  PIN A\n    DIRECTION INPUT ;\n"
    src = _write(tmp_path, "src.lef", bad)
    dst = tmp_path / "dst.lef"

    with pytest.raises(RuntimeError, match="unterminated PIN"):
        patch_lef(src, dst)


def test_output_pin_left_alone(tmp_path: Path):
    lef = """\
VERSION 5.8 ;
MACRO X
  PIN OUT
    DIRECTION OUTPUT ;
    PORT
      LAYER M1 ;
        RECT 0 0 1 1 ;
    END
  END OUT
END X
END LIBRARY
"""
    src = _write(tmp_path, "src.lef", lef)
    dst = tmp_path / "dst.lef"

    patched, already = patch_lef(src, dst)

    assert patched == 0
    assert already == 0
    # Banner is still prepended even when no injection occurred — strip
    # it before checking that the LEF body has no ANTENNAGATEAREA.
    assert "ANTENNAGATEAREA" not in _strip_banner(dst.read_text())
