import colorsys

STATE_COLORS: dict[str, tuple[int, int, int]] = {
    "working": (0xCC, 0x55, 0x00),
    "stopped": (0x00, 0xCC, 0x00),
    "closed":  (0xCC, 0x00, 0x00),
    "empty":   (0x00, 0x00, 0x00),
}

# 8-color session swatch palette. Most slots derive from HSL with S=0.65,
# L=0.5; a few are direct RGB tweaks (purple, green, red) chosen for clearer
# panel distinction. Hue collisions with the BAR_GREEN/BAR_RED histogram
# colors are acceptable — swatch placement (left column) and code-bar
# placement (right-side stacked) make them visually distinguishable.
#
# The server leases an index to each session for its lifetime so up-to-8
# visible sessions never collide on color (vs. hash-mod-8 which would collide
# ~76% of the time via the birthday paradox).
def _hsl(h_deg: int) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(h_deg / 360.0, 0.5, 0.65)
    return round(r * 255), round(g * 255), round(b * 255)


PALETTE: tuple[tuple[int, int, int], ...] = (
    (240, 140, 40),   # 0: orange
    _hsl(60),         # 1: yellow
    _hsl(180),        # 2: cyan
    (110, 40, 210),   # 3: purple     — fills the blue↔magenta gap, distinct from both via R-shift
    _hsl(240),        # 4: blue
    (40, 200, 40),    # 5: green      — minimal blue so swatch reads green, not cyan-adjacent
    _hsl(300),        # 6: magenta
    (170, 40, 60),    # 7: red        — darker + pink-tinted vs BAR_RED(220,30,30)
)
PALETTE_SIZE = len(PALETTE)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "palette":
        try:
            idx = int(sys.argv[2])
        except ValueError:
            sys.exit(2)
        if not 0 <= idx < PALETTE_SIZE:
            sys.exit(2)
        r, g, b = PALETTE[idx]
        print(f"{r:02x}{g:02x}{b:02x}")
        sys.exit(0)
    sys.stderr.write("usage: colors.py palette <0..7>\n")
    sys.exit(2)
