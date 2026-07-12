import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal

from pipeline import grid, render

gdal.UseExceptions()
COLORMAP = Path(__file__).resolve().parent.parent / "colormap_hsp.txt"


def synthetic_hsp():
    arr = np.full((grid.NY, grid.NX), grid.NORAIN_V, dtype=np.int16)
    arr[1400:1500, 1100:1200] = 500  # 5mm/h 블록
    return arr


def make_qpf_png(td):
    """예측 응답 모사: 250,250,250 배경 + 파란 사각형 RGBA PNG."""
    a = np.full((4, 100, 120), 255, dtype=np.uint8)
    a[0], a[1], a[2] = 250, 250, 250
    a[0, 40:60, 50:70], a[1, 40:60, 50:70], a[2, 40:60, 50:70] = 0, 74, 245
    drv = gdal.GetDriverByName("GTiff")
    t = str(Path(td) / "q.tif")
    ds = drv.Create(t, 120, 100, 4, gdal.GDT_Byte)
    for b in range(4):
        ds.GetRasterBand(b + 1).WriteArray(a[b])
    ds = None
    p = str(Path(td) / "q.png")
    gdal.Translate(p, t, format="PNG")
    return Path(p).read_bytes()


class RenderTest(unittest.TestCase):
    def test_hsp_png_has_alpha_and_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "o.png"
            b = render.render_hsp_png(synthetic_hsp(), out, Path(td), COLORMAP)
            self.assertGreater(render.opaque_count(out), 0)
            self.assertAlmostEqual(b["west"], 118.848103, places=2)
            ds = gdal.Open(str(out))
            self.assertEqual(ds.RasterCount, 4)
            self.assertEqual(ds.RasterXSize, 2048)

    def test_hsp_png_is_smoothed(self):
        """bilinear 워프 + 선형 컬러램프 → 경계에 그라디언트가 생겨 색상 수가
        많아야 한다(nearest+near였다면 소수 색상만 나옴)."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "smooth.png"
            render.render_hsp_png(synthetic_hsp(), out, Path(td), COLORMAP)
            ds = gdal.Open(str(out))
            a = ds.ReadAsArray()  # (4, H, W)
            rgba = np.moveaxis(a, 0, -1)
            unique = np.unique(rgba.reshape(-1, 4), axis=0)
            self.assertGreater(len(unique), 20)

    def test_qpf_background_removed(self):
        with tempfile.TemporaryDirectory() as td:
            png = make_qpf_png(td)
            out = Path(td) / "fc.png"
            cov = {"sx": -386015.5, "sy": 4821054.0,
                   "ex": 585174.375, "ey": 3799270.5}
            b = render.qpf_to_overlay_png(png, cov, out, Path(td))
            n = render.opaque_count(out)
            self.assertGreater(n, 0)
            ds = gdal.Open(str(out))
            total = ds.RasterXSize * ds.RasterYSize
            self.assertLess(n / total, 0.2)  # 배경이 투명해졌는가
            self.assertTrue(120.0 < b["west"] < 122.5)
            self.assertTrue(39.0 < b["north"] < 41.0)


if __name__ == "__main__":
    unittest.main()
