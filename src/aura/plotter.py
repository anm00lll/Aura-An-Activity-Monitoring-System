from __future__ import annotations

from typing import Dict

import matplotlib.pyplot as plt


def show_pie_summary(stats: Dict[str, int]) -> None:
    """Show a pie chart of focused vs unfocused seconds on the main thread, non-blocking."""

    # Ensure an interactive backend (TkAgg) if available
    try:
        plt.switch_backend("TkAgg")
    except Exception:
        pass

    focused = stats.get("focused_seconds", 0)
    unfocused = stats.get("unfocused_seconds", 0)
    total = focused + unfocused
    if total <= 0:
        # Avoid divide-by-zero; show an empty chart
        focused = 0
        unfocused = 0
    labels = ["Focused", "Unfocused"]
    sizes = [focused, unfocused]
    colors = ["#2ecc71", "#e74c3c"]
    explode = (0.05, 0.05)

    fig, ax = plt.subplots()  # type: ignore[call-arg]
    ax.pie(
        sizes,
        explode=explode,
        labels=labels,
        autopct=lambda p: f"{p:.1f}%" if total else "0%",
        shadow=False,
        startangle=140,
        colors=colors,
        textprops={"color": "black"},
    )  # type: ignore[call-arg]
    ax.axis("equal")  # Equal aspect ratio ensures that pie is drawn as a circle.
    ax.set_title("AURA Session Summary")  # type: ignore[call-arg]
    # Non-blocking show to avoid freezing Tk
    try:
        plt.show(block=False)  # type: ignore[call-arg]
    except TypeError:
        # Older Matplotlib versions may not support block kwarg; fallback
        plt.show()  # type: ignore[call-arg]
