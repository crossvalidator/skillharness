"""Example: a line chart with the finding in the title and a direct label instead of a legend."""

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
RAIN_MM = [100, 85, 55, 65, 55, 20, 5, 5, 35, 95, 125, 120]  # illustrative values for Lisbon


def draw(ax):
    (line,) = ax.plot(MONTHS, RAIN_MM, linewidth=2.2)
    peak = RAIN_MM.index(max(RAIN_MM))
    ax.scatter([MONTHS[peak]], [RAIN_MM[peak]], color=line.get_color(), zorder=3)
    ax.text(MONTHS[peak], RAIN_MM[peak] + 6, f"{RAIN_MM[peak]} mm", ha="center", color=line.get_color())
    ax.set_title("Lisbon's rain peaks in November and all but stops in summer")
    ax.set_ylabel("Rain (mm per month)")
    ax.set_xlabel("")
    ax.set_ylim(0, 150)
    ax.text(0, -0.12, "Illustrative values", transform=ax.transAxes, fontsize=8, color="#9aa0a6")
