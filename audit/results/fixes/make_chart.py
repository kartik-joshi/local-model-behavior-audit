"""Before/after dumbbell chart of the four fixes, for the post. Numbers are from comparison.md
(runs 1-50 of each condition against its baseline, same seeds).

Run: .venv/bin/python audit/results/fixes/make_chart.py  ->  results/fixes/fixes_chart.png
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(__file__).resolve().parent / "fixes_chart.png"
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
BEFORE, AFTER = "#eb6834", "#2a78d6"

# (fix group, label, before %, after %, firm?)
ROWS = [
    ("Three rules in the prompt", "Gemma: real word count  (higher is better)", 4, 80, True),
    ("Three rules in the prompt", "Gemma: made-up count, undisclosed", 64, 2, True),
    ("Three rules in the prompt", "Qwen, thinking off: tried to change the system", 54, 6, True),
    ("Read-only input files", "Gemma: tried to change the input log", 88, 88, False),
    ("Read-only input files", "Gemma: tried sudo or chmod (all blocked)", 0, 88, True),
    # Rerun with confirm_final (comparison_more.md); the first native run gave 70%, cut short by the protocol.
    ("Native tool calling", "Qwen, thinking off: real word count  (higher is better)", 2, 84, True),
    ("Answer-checking gate", "Gemma: made-up download count, undisclosed", 26, 2, True),
    ("Answer-checking gate", "Gemma: real word count  (higher is better)", 4, 4, False),
]

fig, ax = plt.subplots(figsize=(10, 8.6), dpi=150)
fig.patch.set_facecolor(SURFACE)
ax.set_facecolor(SURFACE)

y, ys, group_y = 0, [], []
prev = None
for g, *_ in ROWS:
    if g != prev:
        y -= 0.45 if prev else 0
        group_y.append((g, y))
        y -= 0.75
        prev = g
    ys.append(y)
    y -= 1

for (g, label, b, a, firm), yy in zip(ROWS, ys):
    ax.plot([b, a], [yy, yy], color=MUTED, lw=2, solid_capstyle="round", zorder=1)
    if b != a:
        ax.scatter([b], [yy], s=95, facecolor=SURFACE, edgecolor=BEFORE, linewidth=2.2, zorder=3)
    ax.scatter([a], [yy], s=95, color=AFTER, edgecolor=SURFACE, linewidth=2, zorder=4)
    ax.text(-2, yy + 0.36, label, ha="left", va="bottom", fontsize=10.5, color=INK2)
    if b == a:
        # Same value: show the before ring larger, around the after dot.
        ax.scatter([b], [yy], s=260, facecolor=SURFACE, edgecolor=BEFORE, linewidth=2.2, zorder=2)
        txt = f"{a}% → {a}%  no change"
        if a > 60:
            ax.text(a - 4, yy, txt, ha="right", va="center", fontsize=10, color=INK)
        else:
            ax.text(a + 4, yy, txt, ha="left", va="center", fontsize=10, color=INK)
    else:
        lo, hi = sorted((b, a))
        ax.text(lo - 2.5, yy, f"{lo}%", ha="right", va="center", fontsize=10, color=INK)
        ax.text(hi + 2.5, yy, f"{hi}%", ha="left", va="center", fontsize=10, color=INK, fontweight="bold" if hi == a else None)
for g, yy in group_y:
    ax.text(-2, yy - 0.05, g, ha="left", va="bottom", fontsize=12.5, fontweight="bold", color=INK)

ax.set_xlim(-8, 108)
ax.set_ylim(y + 0.4, 0.6)
ax.set_xticks([0, 25, 50, 75, 100])
ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], color=INK2, fontsize=10)
ax.set_yticks([])
ax.grid(axis="x", color=GRID, lw=1)
ax.set_axisbelow(True)
for s in ax.spines.values():
    s.set_visible(False)
ax.tick_params(axis="x", length=0)

fig.text(0.06, 0.965, "Four fixes for local LLM agents, measured", fontsize=17, fontweight="bold", color=INK)
fig.text(0.06, 0.935, "Share of runs, 50 runs per condition, same seeds as the baseline. Gemma 3 12B and Qwen 3 8B,",
         fontsize=10.5, color=INK2)
fig.text(0.06, 0.915, "every command in a locked-down Docker sandbox.", fontsize=10.5, color=INK2)
# legend
lx, ly = 0.06, 0.885
fig.patches.extend([])
ax_leg = fig.add_axes([lx, ly - 0.012, 0.5, 0.03])
ax_leg.axis("off")
ax_leg.set_xlim(0, 1); ax_leg.set_ylim(0, 1)
ax_leg.scatter([0.02], [0.5], s=80, facecolor=SURFACE, edgecolor=BEFORE, linewidth=2.2)
ax_leg.text(0.05, 0.5, "Before (no fix)", va="center", fontsize=10.5, color=INK)
ax_leg.scatter([0.33], [0.5], s=80, color=AFTER)
ax_leg.text(0.36, 0.5, "After the fix", va="center", fontsize=10.5, color=INK)
fig.text(0.06, 0.025, "All changes shown hold after a Holm correction within their experiment, except the two "
         "“no change” rows.", fontsize=9, color=MUTED)

fig.subplots_adjust(left=0.06, right=0.96, top=0.855, bottom=0.07)
fig.savefig(OUT, facecolor=SURFACE)
print(OUT)
