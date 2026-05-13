import colorsys

STATE_COLORS: dict[str, tuple[int, int, int]] = {
    "working": (0xCC, 0x55, 0x00),
    "stopped": (0x00, 0xCC, 0x00),
    "closed":  (0xCC, 0x00, 0x00),
    "empty":   (0x00, 0x00, 0x00),
}

# 8-color session swatch palette in HSL with S=0.65, L=0.5. Hues are picked
# from the non-red, non-green zones of the wheel so swatches never visually
# collide with BAR_RED/BAR_GREEN in the histogram. Two warm (orange, yellow),
# six cool (cyan → pink), 30° apart within each cluster.
#
# The server leases an index to each session for its lifetime so up-to-8
# visible sessions never collide on color (vs. hash-mod-8 which would collide
# ~76% of the time via the birthday paradox).
def _hsl(h_deg: int) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(h_deg / 360.0, 0.5, 0.65)
    return round(r * 255), round(g * 255), round(b * 255)


PALETTE: tuple[tuple[int, int, int], ...] = (
    (240, 140, 40),   # 0: orange     — brighter than _hsl(30) so it doesn't blend into brown
    _hsl(60),         # 1: yellow
    _hsl(180),        # 2: cyan
    (143, 80, 36),    # 3: brown      — direct RGB, not reachable at default L/S
    _hsl(240),        # 4: blue
    (60, 230, 100),   # 5: green      — brighter + cyan-tinted vs BAR_GREEN(0,200,0)
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
