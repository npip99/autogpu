#!/usr/bin/env python3
"""Render the compute_array_abut architecture, showing where the broadcast
fanouts break pure-abutment.

Two side-by-side diagrams:
  Left:  current architecture (half pure-abutment, half fanout from cmd_unit)
  Right: proposed pure abutment (every signal flows S→N / W→E via skew chain)

The blocking issue is the per-row push_a_bytes[i] needing to reach each
skew_a[i] separately — cmd_unit emits a 256-bit vector, each row taps its
own byte. Without an abutment chain that carries the 256-bit vector
through skew_lanes, this is a 1→32 fanout from cmd_unit's output pin.
"""
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

fig, (ax_now, ax_goal) = plt.subplots(1, 2, figsize=(18, 9))

# Common: die outline + macros
def draw_macros(ax, title):
    ax.set_title(title, fontsize=13, weight="bold")
    ax.set_xlim(-50, 1350)
    ax.set_ylim(-50, 1350)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    # Die
    ax.add_patch(mpatches.Rectangle((0, 0), 1300, 1300,
                 fill=False, edgecolor="black", linewidth=2))
    # cmd_unit at SW
    ax.add_patch(mpatches.Rectangle((40, 40), 45, 45,
                 facecolor="#d62728", edgecolor="black", linewidth=1))
    ax.text(62, 62, "cmd", fontsize=8, ha="center", va="center", color="white", weight="bold")
    # skew_a column (W edge)
    for i in range(32):
        y = 127 + i * 35
        ax.add_patch(mpatches.Rectangle((95, y), 22, 22,
                     facecolor="#1f77b4", edgecolor="black", linewidth=0.4))
    # skew_b row (S edge)
    for j in range(32):
        x = 127 + j * 35
        ax.add_patch(mpatches.Rectangle((x, 95), 22, 22,
                     facecolor="#2ca02c", edgecolor="black", linewidth=0.4))
    # mac mesh 32x32 abutted
    for i in range(32):
        for j in range(32):
            x = 127 + j * 35
            y = 127 + i * 35
            ax.add_patch(mpatches.Rectangle((x, y), 35, 35,
                         facecolor="#ffe082", edgecolor="black", linewidth=0.05))
    # Labels
    ax.text(100, 1280, "skew_a column", fontsize=9, color="#1f77b4", weight="bold")
    ax.text(700, 100, "skew_b row", fontsize=9, color="#2ca02c", ha="center", weight="bold")
    ax.text(700, 700, "mac mesh 32×32 (abutted)", fontsize=11, ha="center", weight="bold")

# ===== LEFT: current architecture =====
draw_macros(ax_now, "CURRENT — half pure-abutment, half fanout")

# Red fanout arrows from cmd_unit to all 32 skew_a's (push_a_bytes[i])
for i in range(0, 32, 2):  # every other for clarity
    y_dest = 127 + i * 35 + 11
    ax_now.annotate("",
        xy=(95, y_dest), xytext=(85, 62),
        arrowprops=dict(arrowstyle="->", color="red", alpha=0.4, lw=1.2))
# Same red fanout from cmd_unit to all 32 skew_b's
for j in range(0, 32, 2):
    x_dest = 127 + j * 35 + 11
    ax_now.annotate("",
        xy=(x_dest, 95), xytext=(85, 85),
        arrowprops=dict(arrowstyle="->", color="red", alpha=0.4, lw=1.2))

ax_now.text(700, -30,
    "RED: 64 long fanouts from cmd_unit (SW corner) to every skew_a / skew_b\n"
    "Resizer inserts buffers along these wires — buffers congest the W & S mac boundaries\n"
    "(diagnose_grt.sh confirmed: stuck nets at iter 10 = ALL resizer buffers on these paths)",
    fontsize=9, ha="center", color="red")

# ===== RIGHT: proposed pure abutment =====
draw_macros(ax_goal, "PROPOSED — pure abutment via skew_lane chain")

# Single arrow from cmd_unit to skew_a[0] (only one short connection)
ax_goal.annotate("",
    xy=(95, 127 + 11), xytext=(85, 85),
    arrowprops=dict(arrowstyle="->", color="green", lw=2.5))
ax_goal.annotate("",
    xy=(127, 95), xytext=(85, 85),
    arrowprops=dict(arrowstyle="->", color="green", lw=2.5))

# Chain arrows up skew_a column (one cell to next — abutment)
for i in range(0, 31, 4):
    y0 = 127 + i * 35 + 22
    y1 = 127 + (i + 1) * 35
    ax_goal.annotate("",
        xy=(106, y1), xytext=(106, y0),
        arrowprops=dict(arrowstyle="->", color="green", alpha=0.6, lw=1.5))

# Chain arrows along skew_b row
for j in range(0, 31, 4):
    x0 = 127 + j * 35 + 22
    x1 = 127 + (j + 1) * 35
    ax_goal.annotate("",
        xy=(x1, 106), xytext=(x0, 106),
        arrowprops=dict(arrowstyle="->", color="green", alpha=0.6, lw=1.5))

# Short arrows from skew_lanes into mac mesh (these already exist — abutment)
for i in [0, 8, 16, 24, 31]:
    y = 127 + i * 35 + 17
    ax_goal.annotate("",
        xy=(127, y), xytext=(117, y),
        arrowprops=dict(arrowstyle="->", color="gray", alpha=0.5, lw=0.8))

ax_goal.text(700, -30,
    "GREEN: each broadcast hops one macro at a time, registered per stage\n"
    "Wire length per hop: ~35 µm (1 mac pitch) — short enough to close at any frequency\n"
    "Requires re-hardening skew_lane_a/b with 264-bit chain ports on N/S (a) and W/E (b) edges",
    fontsize=9, ha="center", color="green")

fig.suptitle("compute_array_abut: why current 32×32 build fails GRT",
             fontsize=14, weight="bold")

out = "/home/ubuntu/pipitone/gpu3/build/render/architecture_problem.png"
plt.tight_layout()
fig.savefig(out, dpi=120, bbox_inches="tight")
print(f"wrote {out}")
