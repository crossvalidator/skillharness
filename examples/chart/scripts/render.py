"""Render chart.py to chart.png and chart.svg and print a check log.

    python render.py chart.py [--wide] [--dark]

chart.py must define `draw(ax)`. The renderer owns the figure: size, theme, spines, saving. The
checks are the rules in SKILL.md that can be checked mechanically; a failed check exits 1 but the
PNG is still saved so the model can look at it.
"""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ACCENT = "#5e6ad2"
GREY = "#9aa0a6"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--wide", action="store_true")
    parser.add_argument("--dark", action="store_true")
    args = parser.parse_args()
    path = Path(args.file)

    if args.dark:
        plt.style.use("dark_background")
    plt.rcParams.update({"font.size": 11, "axes.titlesize": 13, "axes.titleweight": "bold", "axes.titlelocation": "left"})
    plt.rcParams["axes.prop_cycle"] = plt.cycler(color=[ACCENT, GREY, "#c9ccd1", "#6e7781", "#d0d4dc"])

    try:
        namespace = runpy.run_path(str(path))
    except Exception as error:  # noqa: BLE001
        print(f"FAILED: {path.name} raised {type(error).__name__}: {error}")
        return 1
    draw = namespace.get("draw")
    if not callable(draw):
        print(f"FAILED: {path.name} must define draw(ax)")
        return 1

    fig, ax = plt.subplots(figsize=(11, 6.2) if args.wide else (7, 5), dpi=120)
    try:
        draw(ax)
    except Exception as error:  # noqa: BLE001
        print(f"FAILED: draw(ax) raised {type(error).__name__}: {error}")
        return 1
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    failed = []
    title = ax.get_title(loc="left") or ax.get_title()
    if not title.strip():
        failed.append("no title: set one that states the finding")
    if not ax.get_xlabel().strip() and not ax.get_xticklabels():
        failed.append("no x label")
    if not ax.get_ylabel().strip():
        failed.append("no y label: name the quantity and its unit")
    series = len(ax.lines) + len(ax.containers) + len(ax.collections)
    if series == 0:
        failed.append("nothing drawn")
    if len(ax.lines) + len(ax.containers) > 5:
        failed.append(f"{len(ax.lines) + len(ax.containers)} series: at most five")
    for container in ax.containers:
        if ax.get_ylim()[0] > 0 and any(getattr(bar, "get_height", lambda: 0)() > 0 for bar in container):
            failed.append("bars do not start at zero")
            break

    fig.tight_layout()
    fig.savefig("chart.png")
    fig.savefig("chart.svg")
    w, h = fig.get_size_inches() * fig.dpi
    print(f"title: {title.strip() or '(none)'}")
    print(f"series: {series}")
    for problem in failed:
        print(f"FAILED: {problem}")
    print(f"saved chart.png ({int(w)}x{int(h)}) and chart.svg")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
