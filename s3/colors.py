"""Port of server/colors.py for CircuitPython.

Same PALETTE values as the server module (they must match bit-for-bit so the
LED tile color and the dev-box terminal tab tint stay in sync). The HSL→RGB
math is inlined since CircuitPython doesn't ship colorsys.
"""

STATE_COLORS = {
    "working": (0xCC, 0x55, 0x00),
    "stopped": (0x00, 0xCC, 0x00),
    "closed":  (0xCC, 0x00, 0x00),
    "empty":   (0x00, 0x00, 0x00),
}


def _v(m1, m2, hue):
    hue = hue % 1.0
    if hue < 1.0 / 6.0:
        return m1 + (m2 - m1) * hue * 6.0
    if hue < 0.5:
        return m2
    if hue < 2.0 / 3.0:
        return m1 + (m2 - m1) * (2.0 / 3.0 - hue) * 6.0
    return m1


def _hls_to_rgb(h, l, s):
    if s == 0.0:
        return l, l, l
    if l <= 0.5:
        m2 = l * (1.0 + s)
    else:
        m2 = l + s - (l * s)
    m1 = 2.0 * l - m2
    return _v(m1, m2, h + 1.0 / 3.0), _v(m1, m2, h), _v(m1, m2, h - 1.0 / 3.0)


def _hsl(h_deg):
    r, g, b = _hls_to_rgb(h_deg / 360.0, 0.5, 0.65)
    return round(r * 255), round(g * 255), round(b * 255)


PALETTE = (
    (240, 140, 40),   # 0: orange
    _hsl(60),         # 1: yellow
    _hsl(180),        # 2: cyan
    (110, 40, 210),   # 3: purple — fills the blue↔magenta gap
    _hsl(240),        # 4: blue
    (40, 200, 40),    # 5: green  — minimal blue so swatch reads green
    _hsl(300),        # 6: magenta
    (170, 40, 60),    # 7: red    — darker + pink-tinted vs BAR_RED(220,30,30)
)
PALETTE_SIZE = len(PALETTE)
