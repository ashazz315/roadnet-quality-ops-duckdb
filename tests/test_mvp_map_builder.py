from __future__ import annotations

import unittest

import pandas as pd

from quality_mvp.map_builder import build_record_map, map_html


class MapBuilderTests(unittest.TestCase):
    def test_map_contains_each_valid_record(self) -> None:
        frame = pd.DataFrame(
            [
                {"record_id": "A1", "longitude": 116.4, "latitude": 39.9, "severity": "high"},
                {"record_id": "A2", "longitude": 116.5, "latitude": 39.8, "severity": "low"},
            ]
        )
        html = map_html(frame)
        self.assertIn("A1", html)
        self.assertIn("A2", html)
        location = build_record_map(frame).location
        self.assertAlmostEqual(location[0], 39.85)
        self.assertAlmostEqual(location[1], 116.45)

    def test_empty_map_uses_default_center(self) -> None:
        self.assertEqual(build_record_map(pd.DataFrame()).location, [39.9042, 116.4074])


if __name__ == "__main__":
    unittest.main()
