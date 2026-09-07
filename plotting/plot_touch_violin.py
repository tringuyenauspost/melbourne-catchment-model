"""Volume-weighted distribution of buildings touched, per facility."""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

_ROOT = Path(__file__).resolve().parents[1]              # outputs/ is at the repo root
CSV = _ROOT / "outputs/path_census_sankey/top10_touch_profile.csv"
OUT = _ROOT / "outputs/path_census_sankey/touch_violin.png"
CM = 1 / 2.54
# sized for the content area of a 16:9 slide (33.87 x 19.05 cm) with room for a title above
W_CM, H_CM, DPI = 30.0, 13.5, 300
COUNTS = ["0", "1", "2", "3", "4+"]                          # 4+ plotted at 4 -> lower bound
ORDER = ["STARTRACK MELBOURNE", "STARTRACK MELBOURNE SOUTH EAST", "STARTRACK MELBOURNE DFPC",
         "TULLAMARINE PDC", "MELBOURNE NTH PARCEL FACILITY", "SUNSHINE WEST PDC", "BAYSWATER PDC", "OAKLEIGH SOUTH PDC",
         "DAREBIN PDC", "DANDENONG SOUTH PDC"]
SHORT = {"STARTRACK MELBOURNE": "ST Melbourne", "STARTRACK MELBOURNE SOUTH EAST": "ST MSE",
         "STARTRACK MELBOURNE DFPC": "ST DFPC", "TULLAMARINE PDC": "Tullamarine\nPDC",
         "MELBOURNE NTH PARCEL FACILITY":"Melbourne North\nPDC",
         "SUNSHINE WEST PDC": "Sunshine West\nPDC", "BAYSWATER PDC": "Bayswater\nPDC",
         "OAKLEIGH SOUTH PDC": "Oakleigh South\nPDC", "DAREBIN PDC": "Darebin\nPDC",
         "DANDENONG SOUTH PDC": "Dandenong South\nPDC"}
# ordinal ramp, light->dark: more touches = darker (steps 250/350/450/550/700)
TOUCH = dict(zip(COUNTS, ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]))

df = pd.read_csv(CSV).set_index("site")
shares = df.loc[ORDER, COUNTS].rename(index=SHORT)
shares = shares.div(shares.sum(axis=1), axis=0) * 100                  # % of each row's volume
means = (shares * range(5)).sum(axis=1) / 100                          # mean touches

rows = [(site, i) for site, r in shares.iterrows()                     # weighted sample
        for i, pct in enumerate(r) for _ in range(round(pct * 10))]
plot = pd.DataFrame(rows, columns=["site", "touches"])

sns.set_theme(style="ticks", font_scale=1.0)
fig, ax = plt.subplots(figsize=(W_CM * CM, H_CM * CM))
sns.violinplot(plot, x="site", y="touches", order=shares.index, cut=0, bw_adjust=0.45,
               density_norm="width", color="#e6e5e0", linecolor="#b5b4ad",
               linewidth=1.1, inner=None, saturation=1, ax=ax)
for x, site in enumerate(shares.index):
    for i, (b, pct) in enumerate(zip(COUNTS, shares.loc[site])):
        ax.plot(x, i, "o", ms=6, color=TOUCH[b], mec="white", mew=0.8, zorder=6)
        ax.text(x + 0.11, i, f"{pct:.0f}%", fontsize=8, color="#52514e", va="center")
    ax.plot(x, means[site], "D", ms=6.5, color="#0b0b0b", mec="white", mew=0.9, zorder=7)
    ax.text(x, 4.62, f"{means[site]:.2f}", fontsize=10, color="#0b0b0b", ha="center",
            fontweight="bold")

ax.axvline(2.5, color="#b5b4ad", lw=1.1, ls=(0, (4, 3)))     # ST | AP sites
ax.text(-0.52, 4.62, "mean\ntouches", fontsize=8, color="#52514e", style="italic",
        va="center", linespacing=1.1)
ax.set_yticks(range(5), COUNTS)
ax.set(xlabel="", ylim=(-0.45, 4.85))
ax.set_ylabel("Buildings touched before the delivery depot", fontsize=10)
ax.set_xticks(range(len(shares)), shares.index, fontsize=9.5)
ax.tick_params(axis="y", labelsize=10)
ax.set_title("Buildings touched per article, by facility (volume-weighted)",
             loc="left", fontsize=13, fontweight="bold", pad=16)
handles = [plt.Line2D([], [], marker="D", ls="", ms=6, color="#0b0b0b", mec="white",
                      label="mean"),
           plt.Line2D([], [], marker="o", ls="", ms=6, color="#2a78d6", mec="white",
                      label="% of the facility's volume")]
ax.legend(handles=handles, title=None, frameon=False, fontsize=9.5, loc="upper right",
          ncol=2, columnspacing=1.2, handletextpad=0.3, bbox_to_anchor=(1, 1.10))
sns.despine()
fig.tight_layout()
fig.savefig(OUT, dpi=DPI)
print(means.round(2).to_string())
