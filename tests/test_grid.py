import tempfile
import unittest
from pathlib import Path

import numpy as np

from pipeline import grid


class GridTest(unittest.TestCase):
    def synthetic_raw(self):
        arr = np.full((grid.NY, grid.NX), grid.NORAIN_V, dtype="<i2")
        arr[0, 0] = 1234  # 남서 모서리 표식
        return bytes(grid.HEADER_BYTES) + arr.tobytes()

    def test_parse_grid_flips_to_topdown(self):
        out = grid.parse_grid(self.synthetic_raw())
        self.assertEqual(out.shape, (grid.NY, grid.NX))
        # 파일의 첫 행(남쪽)이 top-down 배열에선 마지막 행
        self.assertEqual(out[-1, 0], 1234)
        self.assertEqual(out[0, 0], grid.NORAIN_V)

    def test_parse_grid_rejects_bad_size(self):
        with self.assertRaises(ValueError):
            grid.parse_grid(b"x" * 100)

    def test_roundtrip_bounds(self):
        out = grid.parse_grid(self.synthetic_raw())
        with tempfile.TemporaryDirectory() as td:
            lcc = Path(td) / "l.tif"
            merc = Path(td) / "m.tif"
            grid.write_lcc_tiff(out, lcc)
            grid.warp_to_3857(lcc, merc, nodata=grid.NULL_V)
            b = grid.bounds_4326(merc)
        # PoC 검증값(전국 도메인) 근사 일치
        self.assertAlmostEqual(b["west"], 118.848103, places=2)
        self.assertAlmostEqual(b["south"], 30.107777, places=2)
        self.assertAlmostEqual(b["east"], 133.568265, places=2)
        self.assertAlmostEqual(b["north"], 43.579492, places=2)


if __name__ == "__main__":
    unittest.main()
