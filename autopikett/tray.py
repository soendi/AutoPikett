"""Systray-Symbol: grüner Punkt = Pikett ein, roter Punkt = Pikett aus."""

from __future__ import annotations

from PIL import Image, ImageDraw

from autopikett.core import TRAY_COLOR_OFF, TRAY_COLOR_ON

__all__ = ["make_tray_image"]

SIZE = 64


def make_tray_image(pikett_on: bool, size: int = SIZE) -> Image.Image:
    """Punkt in Grün (Pikett ein) oder Rot (Pikett aus) auf transparentem Grund."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    margin = size * 0.12
    border = max(2, size // 14)
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=TRAY_COLOR_ON if pikett_on else TRAY_COLOR_OFF,
        outline="#ffffff",
        width=border,
    )
    return image
