# Style

## Palette

The renderer sets the colour cycle: accent `#5e6ad2` first, then greys. Use the accent for the one
thing the title is about and grey for context. Pass colours explicitly when the order of series
would put the accent on the wrong one.

## Which chart

| Question | Chart |
|---|---|
| How did it change over time? | Line. One line per series, direct labels at the right end. |
| How do these compare? | Horizontal bars, sorted by value, the highlighted one in the accent. |
| What is the share of a whole? | Stacked bar, one bar, at most four parts. Never a pie. |
| How is it distributed? | Histogram, or a dot strip when there are under 50 values. |
| How do two quantities relate? | Scatter, with the one or two notable points labelled. |

## Small details

- Format thousands with a thin space or a comma, and money with the symbol in the axis label, not on
  every tick: `ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))`.
- Percent axes: `PercentFormatter()` on the axis, and the label says "share of ...".
- Years as integers on the x axis, never `2,021`.
- Rotate x tick labels only as a last resort; prefer horizontal bars when labels are long.
- Put the source or "illustrative values" in small grey text at the bottom left:
  `ax.text(0, -0.15, "Source: ...", transform=ax.transAxes, fontsize=8, color="#9aa0a6")`.
- Direct label: `ax.text(x[-1], y[-1], " name", va="center", color=line.get_color())`.
