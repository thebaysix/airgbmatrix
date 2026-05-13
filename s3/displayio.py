"""Dev-box mock of CircuitPython's `displayio` module.

Implements just enough of Bitmap / Palette / TileGrid / Group / Display to
exercise the `Canvas` shim and `render_frame` on the dev box. When deployed to
real hardware this file is NOT copied to CIRCUITPY/ — the firmware-built-in
displayio takes over with the exact same API surface.

Adds one extra: `TerminalDisplay`, which composites the root group and paints
ANSI 24-bit half-blocks to stdout. Real displayio doesn't have this.
"""
import sys


class Bitmap:
    """2D index buffer. Stores one int per pixel; the value is a palette index.

    Real displayio rounds value_count to the nearest power of two (1, 2, 4, 8,
    16, 32, 64, 128, 256) for storage layout. We don't bother — Python list of
    list of int.
    """

    def __init__(self, width, height, value_count):
        self.width = width
        self.height = height
        self.value_count = value_count
        self._data = [[0] * width for _ in range(height)]

    def _xy(self, key):
        if isinstance(key, tuple):
            return key[0], key[1]
        return key % self.width, key // self.width

    def __setitem__(self, key, value):
        x, y = self._xy(key)
        if 0 <= x < self.width and 0 <= y < self.height:
            self._data[y][x] = value

    def __getitem__(self, key):
        x, y = self._xy(key)
        return self._data[y][x]


class Palette:
    """Indexed color palette. Real displayio takes 0xRRGGBB ints (or 3-tuples
    in some firmware versions); we accept either."""

    def __init__(self, n):
        self._colors = [0] * n

    def _to_int(self, value):
        if isinstance(value, int):
            return value
        if isinstance(value, (tuple, list)) and len(value) >= 3:
            r, g, b = value[0], value[1], value[2]
            return (int(r) << 16) | (int(g) << 8) | int(b)
        raise TypeError("palette value must be int 0xRRGGBB or (r,g,b)")

    def __setitem__(self, idx, value):
        self._colors[idx] = self._to_int(value)

    def __getitem__(self, idx):
        return self._colors[idx]

    def __len__(self):
        return len(self._colors)


class TileGrid:
    def __init__(self, bitmap, *, pixel_shader, x=0, y=0):
        self.bitmap = bitmap
        self.pixel_shader = pixel_shader
        self.x = x
        self.y = y


class Group:
    def __init__(self):
        self._items = []

    def append(self, item):
        self._items.append(item)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


def release_displays():
    pass


class TerminalDisplay:
    """Stand-in for the matrixportal display object. `refresh()` walks the
    root group's TileGrids and paints to the terminal."""

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.root_group = None
        self.auto_refresh = False
        self._first = True

    def show(self, group):  # legacy displayio API; some examples still use it
        self.root_group = group

    def refresh(self, *, target_frames_per_second=None, minimum_frames_per_second=None):
        if self.root_group is None:
            return
        buf = [[(0, 0, 0)] * self.width for _ in range(self.height)]
        for tile in self.root_group:
            if not isinstance(tile, TileGrid):
                continue
            bm, pal = tile.bitmap, tile.pixel_shader
            for y in range(bm.height):
                for x in range(bm.width):
                    idx = bm._data[y][x]
                    color = pal._colors[idx] if 0 <= idx < len(pal._colors) else 0
                    r = (color >> 16) & 0xFF
                    g = (color >> 8) & 0xFF
                    b = color & 0xFF
                    px_x = tile.x + x
                    px_y = tile.y + y
                    if 0 <= px_x < self.width and 0 <= px_y < self.height:
                        buf[px_y][px_x] = (r, g, b)
        out = []
        if self._first:
            out.append("\x1b[2J\x1b[?25l")
            self._first = False
        out.append("\x1b[H")
        for row in range(0, self.height, 2):
            for col in range(self.width):
                top = buf[row][col]
                bot = buf[row + 1][col] if row + 1 < self.height else (0, 0, 0)
                out.append(
                    "\x1b[38;2;{};{};{}m\x1b[48;2;{};{};{}m▀".format(
                        top[0], top[1], top[2], bot[0], bot[1], bot[2]
                    )
                )
            out.append("\x1b[0m\n")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
