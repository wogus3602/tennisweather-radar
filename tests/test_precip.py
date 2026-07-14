import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from pipeline import grid, precip, render
from tests.test_render import QPF_COV, make_qpf_png

gdal.UseExceptions()


def legacy_overlay_png(png_bytes, cov, td, out_width=2048):
    """수정 전 예측 렌더 경로(RGBA 팔레트를 near로 워프·확대)를 그대로 재현.

    level[] 회귀의 기준(오라클). 표시용 PNG를 부드럽게 만드는 변경이 강수 타이밍
    배너("약 30분 후 비 시작")를 먹이는 이 값을 건드리면 여기서 잡힌다.
    """
    raw = Path(td) / "legacy_raw.png"
    raw.write_bytes(png_bytes)
    a = gdal.Open(str(raw)).ReadAsArray()
    bg = (a[0] == 250) & (a[1] == 250) & (a[2] == 250)
    a[3][bg] = 0
    lcc = str(Path(td) / "legacy_lcc.tif")
    ds = gdal.GetDriverByName("GTiff").Create(lcc, a.shape[2], a.shape[1], 4,
                                              gdal.GDT_Byte)
    srs = osr.SpatialReference()
    srs.ImportFromProj4(grid.LCC0)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform((cov["sx"], (cov["ex"] - cov["sx"]) / a.shape[2], 0,
                        cov["sy"], 0, (cov["ey"] - cov["sy"]) / a.shape[1]))
    for b in range(4):
        ds.GetRasterBand(b + 1).WriteArray(a[b])
    ds.FlushCache()
    ds = None
    merc = str(Path(td) / "legacy_3857.tif")
    grid.warp_to_3857(lcc, merc)                      # near(기본)
    out = str(Path(td) / "legacy.png")
    subprocess.run(["gdal_translate", "-q", "-of", "PNG",                # near
                    "-outsize", str(out_width), "0", merc, out], check=True)
    return out, grid.bounds_4326(merc)


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


class SmoothingRegressionTest(unittest.TestCase):
    """표시용 PNG 스무딩이 강수 레벨 격자를 바꾸면 안 된다.

    렌더가 팔레트 색을 보간하면 파랑↔초록 경계가 섞여 엉뚱한 버킷(level 3 등)
    으로 분류된다. 그래서 표시용(bilinear)과 격자용(near, 순수 팔레트)을 분리했다
    — 이 테스트가 그 분리를 지킨다.
    """

    def test_levels_identical_to_prefix_palette_path(self):
        with tempfile.TemporaryDirectory() as td:
            png_bytes = make_qpf_png(td)
            legacy_png, legacy_bounds = legacy_overlay_png(png_bytes,
                                                           QPF_COV, td)
            before = precip.build_precip_json(legacy_png, legacy_bounds)

            out = Path(td) / "smooth.png"
            bounds, levels = render.qpf_to_overlay_png(
                png_bytes, QPF_COV, out, Path(td))
            after = precip.build_precip_json(levels, bounds)

        # 격자가 비어 있으면 '둘 다 0' 으로 통과하는 함정 — 실제 강도가 실려야 한다
        self.assertGreaterEqual(len({v for v in after["level"] if v}), 2)
        self.assertEqual(before["level"], after["level"])
        self.assertEqual(before, after)   # bounds·nx·ny 등 스키마 전체 동일


if __name__ == "__main__":
    unittest.main()
