---
name: chart
description: Make one clean, honest chart from a short description or a small table of numbers, as a PNG and an SVG. Use when someone wants a chart, plot, graph or figure of some numbers, a trend, a comparison or a distribution, for a report, a slide or a post.
compatibility: Needs Python 3.12+ with matplotlib.
---

# Chart

One chart, one finding. The reader should see the point before reading a word.

## What you make

A file `chart.py` that defines one function:

```python
def draw(ax):
    ...
```

`ax` is a matplotlib Axes. Draw into it and set the title and axis labels. Do not create a figure,
do not call `plt.show()` or `plt.savefig()`: the renderer owns the canvas, the size and the theme,
and saves `chart.png` and `chart.svg`.

The data goes in the file as literals. If the request gives numbers, use them exactly. If it gives
only a description ("rainfall in Lisbon by month"), use plausible values, say so in the title or a
note on the chart ("illustrative"), and say so again in the hand-over.

## Rules

1. **The title states the finding**, not the topic: "Rain peaks in November" rather than "Monthly rainfall".
2. **Axes are labelled with units.** Years, mm, %, €.
3. **At most five series.** Beyond that, pick the ones that matter or make a small-multiples decision in the plan.
4. **Direct labels beat legends** when there are three series or fewer: put the series name at the end of its line.
5. **No chartjunk.** No 3D, no gridlines heavier than the data, no colour that does not carry meaning. The renderer
   already removes the top and right spines.
6. **Start bars at zero.** Lines may zoom; bars may not.
7. **Highlight one thing.** One series or one bar in the accent colour, the rest in grey.

`references/style.md` has the palette and the small details (tick formats, number formats, when to use which
chart type). Read it for anything beyond a plain line or bar chart. `examples/monthly-rain.py` is a finished file
to copy the shape of.

## Workflow

1. Say the plan in one line: chart type, what is highlighted, the finding the title will state.
2. Write `chart.py`.
3. Render it: `python scripts/render.py chart.py`. Read the check log and look at the image.
4. Fix what the log flags and what looks wrong to you: overlapping labels, a title that names the topic instead of
   the finding, a legend where direct labels would do.
5. Hand over with one or two lines on what the chart shows, and any caveat about the data.

Options: `--wide` renders a 16:9 canvas for slides; `--dark` renders on a dark background. Use them only when the
request calls for a slide or a dark page.
