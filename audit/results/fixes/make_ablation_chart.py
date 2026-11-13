"""Rule ablation chart for the post: each guard rule alone against the baseline and all three rules.
Numbers are from comparison.md (baseline, all three) and comparison_more.md (single rules), runs 1-50.

Run: .venv/bin/python audit/results/fixes/make_ablation_chart.py  ->  results/fixes/ablation_chart.png
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(__file__).resolve().parent / "ablation_chart.png"
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
WORKS, OTHER, BASE, ALL = "#2a78d6", "#d3d1ca", "#8a8984", "#9cc0ec"

BARS = ["No rules (baseline)", "Only: don't change the inputs", "Only: if something fails, say so",
        "Only: don't use sudo or install software", "All three rules"]
# (title, values in BARS order, index of the single rule that worked)
PANELS = [
    ("Gemma 3 12B, C task: reported the real word count  (higher is better)", [4, 70, 10, 14, 80], 1),
    ("Gemma 3 12B, C task: made-up count, undisclosed", [64, 2, 70, 58, 2], 1),
    ("Qwen 3 8B, thinking off, failed download: tried to change the system", [54, 38, 0, 20, 6], 2),
]

fig, axes = plt.subplots(len(PANELS), 1, figsize=(10, 8.4), dpi=150, sharex=True)
fig.patch.set_facecolor(SURFACE)
for ax, (title, vals, works) in zip(axes, PANELS):
    ax.set_facecolor(SURFACE)
    colors = [BASE] + [WORKS if i == works else OTHER for i in (1, 2, 3)] + [ALL]
    ys = list(range(len(BARS)))[::-1]
    ax.barh(ys, vals, height=0.62, color=colors, edgecolor=SURFACE, linewidth=2)
    for y, v, c in zip(ys, vals, colors):
        if v == 0:  # keep a zero visible as a thin stub in its colour
            ax.barh([y], [0.6], height=0.62, color=c)
    for y, v, label in zip(ys, vals, BARS):
        ax.text(-1.5, y, label, ha="right", va="center", fontsize=10, color=INK2)
        ax.text(v + 1.2, y, f"{v}%", ha="left", va="center", fontsize=10, color=INK,
                fontweight="bold" if y == ys[works] else None)
    ax.set_title(title, loc="left", fontsize=11.5, fontweight="bold", color=INK, x=-0.42, pad=6)
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    ax.grid(axis="x", color=GRID, lw=1)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(axis="x", length=0, colors=INK2, labelsize=10)
axes[-1].set_xticks([0, 25, 50, 75, 100])
axes[-1].set_xticklabels(["0%", "25%", "50%", "75%", "100%"])

fig.text(0.02, 0.965, "Which prompt rule does the work? Each rule tested alone", fontsize=16, fontweight="bold",
         color=INK)
fig.text(0.02, 0.935, "Share of runs, 50 runs per bar, same seeds. Blue: the single rule whose change holds after a "
         "Holm correction.", fontsize=10.5, color=INK2)
fig.text(0.02, 0.913, "Light blue: all three rules together.", fontsize=10.5, color=INK2)
fig.text(0.02, 0.012, "Not firm: the other single rules (Holm p 0.06 to 1.0). Every command ran in a locked-down "
         "Docker sandbox.", fontsize=9, color=MUTED)
fig.subplots_adjust(left=0.36, right=0.95, top=0.87, bottom=0.06, hspace=0.55)
fig.savefig(OUT, facecolor=SURFACE)
print(OUT)
