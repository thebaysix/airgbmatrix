import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import renderer  # noqa: E402
import term_renderer  # noqa: E402


class RendererTests(unittest.TestCase):
    def test_turn_without_code_changes_paints_no_code_column(self):
        buf = term_renderer.Buffer(renderer.MATRIX_W, renderer.MATRIX_H)
        renderer.render_frame(
            buf,
            [
                {
                    "color_idx": 0,
                    "turns": [
                        {
                            "kind": "turn",
                            "tokens": 100,
                            "ts_epoch": 2_000_000_000,
                            "added": 0,
                            "removed": 0,
                            "added_any": False,
                            "removed_any": False,
                        }
                    ],
                }
            ],
        )

        histogram = [
            pixel
            for row in buf.pixels[: renderer.TILE_H]
            for pixel in row[renderer.COLOR_W : renderer.TILE_W]
        ]
        self.assertIn(renderer.BAR_BRIGHT, histogram)
        self.assertNotIn(renderer.BAR_GREEN, histogram)
        self.assertNotIn(renderer.BAR_RED, histogram)

    def test_debug_context_uses_latest_positive_turn_not_sum(self):
        turns = [
            {"tokens": 100, "ts_epoch": 1},
            {"tokens": 0, "ts_epoch": 3, "kind": "compact"},
            {"tokens": 40, "ts_epoch": 2},
        ]
        self.assertEqual(term_renderer._latest_tokens(turns), 40)


if __name__ == "__main__":
    unittest.main()
