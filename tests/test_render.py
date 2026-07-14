import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal

from pipeline import grid, render

gdal.UseExceptions()
ROOT = Path(__file__).resolve().parent.parent
COLORMAP = ROOT / "colormap_hsp.txt"
# 실제 예측(4.4) 응답의 covariance — 합성 프레임도 운영과 같은 기하(479px 원본이
# 배포 2048px로 ≈4배 확대)를 타야 '4의 배수 런' 지문을 그대로 재현한다.
QPF_COV = {"sx": -437147.96875, "sy": 4816073.5,
           "ex": 580165.875, "ey": 3803257.0}
QPF_NX = 479
QPF_PALETTE = [(0, 74, 245), (0, 255, 0), (255, 255, 0)]  # make_qpf_png 강도 3단


def synthetic_hsp():
    arr = np.full((grid.NY, grid.NX), grid.NORAIN_V, dtype=np.int16)
    arr[1400:1500, 1100:1200] = 500  # 5mm/h 블록
    return arr


def make_qpf_png(td):
    """예측 응답 모사: 250,250,250 배경 + 강도 3단(파랑·초록·노랑) 인접 블록.

    강도가 맞닿은 경계가 있어야 '보간이 실제로 일어나는가'를 측정할 수 있다
    (단색 한 덩어리는 near로 확대해도 색 수가 안 늘어 near/bilinear 구분 불가).
    """
    n = QPF_NX
    a = np.full((4, n, n), 255, dtype=np.uint8)
    a[0], a[1], a[2] = 250, 250, 250
    for i, rgb in enumerate(QPF_PALETTE):
        x0 = 170 + i * 48
        for c in range(3):
            a[c, 180:260, x0:x0 + 48] = rgb[c]
    drv = gdal.GetDriverByName("GTiff")
    t = str(Path(td) / "q.tif")
    ds = drv.Create(t, n, n, 4, gdal.GDT_Byte)
    for b in range(4):
        ds.GetRasterBand(b + 1).WriteArray(a[b])
    ds = None
    p = str(Path(td) / "q.png")
    gdal.Translate(p, t, format="PNG")
    return Path(p).read_bytes()


def content_bbox(png):
    """RGBA PNG → 불투명 영역 bbox로 자른 (H,W) uint32 픽셀값.

    배경(투명 단색)은 행당 런이 하나뿐이라 통계를 삼킨다 — 내용 영역만 본다.
    """
    a = gdal.Open(str(png)).ReadAsArray().astype(np.uint32)
    packed = (a[0] << 24) | (a[1] << 16) | (a[2] << 8) | a[3]
    ys, xs = np.nonzero(a[3] > 0)
    return packed[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def run_lengths(a2d):
    """행 방향 '같은 값이 이어진 길이' 목록. near 확대의 지문(배율의 배수)을 잡는다."""
    out = []
    for row in a2d:
        cut = np.flatnonzero(np.diff(row) != 0)
        out.append(np.diff(np.concatenate(([-1], cut, [row.size - 1]))))
    return np.concatenate(out)


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
            b, _ = render.qpf_to_overlay_png(png, QPF_COV, out, Path(td))
            n = render.opaque_count(out)
            self.assertGreater(n, 0)
            ds = gdal.Open(str(out))
            total = ds.RasterXSize * ds.RasterYSize
            self.assertLess(n / total, 0.2)  # 배경이 투명해졌는가
            self.assertEqual(ds.RasterCount, 4)
            self.assertEqual(ds.RasterXSize, 2048)
            self.assertTrue(120.0 < b["west"] < 122.5)
            self.assertTrue(39.0 < b["north"] < 41.0)

    def test_qpf_png_is_not_blocky(self):
        """예측 오버레이가 4x4 레고 픽셀이면 안 된다.

        예측 원본(≈480px)을 배포 해상도(2048px)로 near 확대하면 동일 픽셀 런이
        전부 배율의 배수가 되고 색은 팔레트 몇 개뿐 — 사용자가 확대했을 때 본
        계단이 바로 이것. bilinear면 경계에 그라디언트가 생겨 길이 1 런과
        중간색이 나타난다(과거 프레임이 이미 그렇게 보인다).
        """
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "fc.png"
            render.qpf_to_overlay_png(make_qpf_png(td), QPF_COV, out, Path(td))
            sub = content_bbox(out)

        self.assertGreater(len(np.unique(sub)), 20,
                           "중간색이 없다 = 팔레트 계단(near 확대)")
        for axis, a2d in (("가로", sub), ("세로", sub.T)):
            r = run_lengths(a2d)
            ones = float((r == 1).mean())
            # 4배 near 확대는 4px보다 짧은 런을 만들 수 없다 — 짧은 런만 보면
            # 전부 4의 배수(=블록 지문). 긴 런(덩어리 내부)은 블록과 무관해 제외.
            short = r[r <= 24]
            mult4 = float((short % 4 == 0).mean()) if short.size else 1.0
            self.assertGreater(ones, 0.2,
                               f"{axis}: 길이 1 런 {ones:.1%} — 보간 안 됨")
            self.assertLess(mult4, 0.5,
                            f"{axis}: 짧은 런의 {mult4:.1%}가 4의 배수 — 블록 잔존")

    def test_qpf_keeps_source_palette_hues(self):
        """스무딩이 기상청 팔레트를 '다른 색'으로 갈아치우면 안 된다.

        레벨(0~4) 스칼라로 환원해 자체 컬러램프를 다시 태우면 매끈해지긴 하나,
        같은 레벨 안의 계조(약비 안의 옅은 파랑↔진한 파랑)가 뭉개져 비구름이
        단색 덩어리가 되고 색조 자체도 바뀐다. 원본 RGBA를 프리멀티플 공간에서
        보간하면 계조와 색조가 모두 남는다 — 그 성질을 고정한다.

        각 강도 블록의 '중심부'(보간이 닿지 않는 안쪽)가 원본 팔레트 색과
        사실상 같은지로 측정한다.
        """
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "fc.png"
            render.qpf_to_overlay_png(make_qpf_png(td), QPF_COV, out, Path(td))
            a = gdal.Open(str(out)).ReadAsArray()

        rgb = np.moveaxis(a[:3], 0, -1)[a[3] > 200].reshape(-1, 3)
        self.assertGreater(len(rgb), 0, "불투명 강수 화소가 없다")
        for want in QPF_PALETTE:
            d = np.abs(rgb.astype(np.int16) - np.array(want)).sum(axis=1)
            self.assertLess(int(d.min()), 24,
                            f"원본 팔레트색 {want} 이 결과에 남아있지 않다 "
                            f"(최근접 색 거리 {int(d.min())})")


if __name__ == "__main__":
    unittest.main()
