import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal

from pipeline import precip

gdal.UseExceptions()


def make_rgba_png(td, painter):
    """painter(a: np.ndarray[4,H,W]) 로 픽셀을 칠한 RGBA PNG 경로 반환."""
    a = np.zeros((4, 90, 70), dtype=np.uint8)
    painter(a)
    drv = gdal.GetDriverByName("GTiff")
    t = str(Path(td) / "s.tif")
    ds = drv.Create(t, 70, 90, 4, gdal.GDT_Byte)
    for b in range(4):
        ds.GetRasterBand(b + 1).WriteArray(a[b])
    ds = None
    p = str(Path(td) / "s.png")
    gdal.Translate(p, t, format="PNG")
    return p


class LevelTest(unittest.TestCase):
    def test_level_of_buckets(self):
        self.assertEqual(precip.level_of(0, 0, 0, 0), 0)      # 투명=무강수
        self.assertEqual(precip.level_of(0, 200, 255, 255), 1)  # 하늘파랑=약
        self.assertEqual(precip.level_of(0, 74, 245, 255), 1)   # 진파랑=약
        self.assertEqual(precip.level_of(0, 255, 0, 255), 2)    # 초록=보통
        self.assertEqual(precip.level_of(255, 255, 0, 255), 3)  # 노랑=강
        self.assertEqual(precip.level_of(255, 50, 0, 255), 4)   # 빨강=매우강


class BuildTest(unittest.TestCase):
    BOUNDS = {"west": 121.0, "south": 32.0, "east": 132.0, "north": 43.0}

    def test_build_schema_and_maxpool(self):
        with tempfile.TemporaryDirectory() as td:
            # 상단(북) 20행에 초록(level2) 블록, 나머지 투명
            def paint(a):
                a[1, :20, :] = 255  # G
                a[3, :20, :] = 255  # A
            png = make_rgba_png(td, paint)
            doc = precip.build_precip_json(png, self.BOUNDS)
        self.assertEqual((doc["nx"], doc["ny"]), (140, 180))
        self.assertEqual(len(doc["level"]), 140 * 180)
        self.assertEqual(doc["west"], 121.0)
        self.assertEqual(doc["north"], 43.0)
        # 북쪽 첫 행은 강수(2), 남쪽 마지막 행은 무강수(0)
        self.assertEqual(doc["level"][0], 2)
        self.assertEqual(doc["level"][-1], 0)
        self.assertTrue(all(0 <= v <= 4 for v in doc["level"]))


if __name__ == "__main__":
    unittest.main()
