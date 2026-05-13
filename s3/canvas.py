"""SetPixel(x, y, r, g, b) shim over a displayio.Bitmap + Palette.

Allocates palette indices on demand and caches the (r,g,b) -> index map so
repeated SetPixel calls with the same color don't re-allocate. The renderer
uses ~16 distinct colors total (8 session hues, white, dim grey, black, plus
the state colors), well under the default 32-entry palette.

This is *the* component most worth testing on the dev box before flashing —
real displayio is fussy about palette overflow, value_count alignment, and
TileGrid bounds.
"""
import displayio


class Canvas:
    def __init__(self, width, height, palette_size=32, brightness=1.0):
        self.width = width
        self.height = height
        self.bitmap = displayio.Bitmap(width, height, palette_size)
        self.palette = displayio.Palette(palette_size)
        self.palette[0] = 0  # index 0 is reserved for black
        self._index = {(0, 0, 0): 0}
        self._palette_size = palette_size
        # HUB75 panels run at outdoor-readable brightness by default; scale
        # every palette entry down so indoor use isn't eye-searing. Cache key
        # stays unscaled so callers don't have to change anything.
        self._brightness = brightness

    def _palette_index(self, r, g, b):
        key = (r, g, b)
        idx = self._index.get(key)
        if idx is not None:
            return idx
        if len(self._index) >= self._palette_size:
            return 0  # palette full — fall back to black; shouldn't happen
        idx = len(self._index)
        sr = int(r * self._brightness)
        sg = int(g * self._brightness)
        sb = int(b * self._brightness)
        self.palette[idx] = (sr << 16) | (sg << 8) | sb
        self._index[key] = idx
        return idx

    def SetPixel(self, x, y, r, g, b):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.bitmap[x, y] = self._palette_index(r, g, b)
