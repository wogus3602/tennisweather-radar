"""프레임 렌더링 — 실황(int16→컬러 PNG), 예측(팔레트 RGBA→부드러운 오버레이)."""
import subprocess

import numpy as np
from osgeo import gdal, osr

from . import grid, precip

gdal.UseExceptions()

_QPF_BG = 250       # 예측 이미지의 불투명 배경 회색값(R=G=B=250)
_QPF_NODATA = 0     # 레벨 0(무강수) = nodata


def render_hsp_png(arr, out_png, workdir, colormap_path, out_width=2048):
    """top-down HSP 배열 → 재투영·컬러맵·투명 PNG. bounds(4326) 반환."""
    lcc = workdir / "hsp_lcc.tif"
    merc = workdir / "hsp_3857.tif"
    color = workdir / "hsp_color.tif"
    grid.write_lcc_tiff(arr, lcc)
    # 관측 강수는 RainViewer식 스무딩(bilinear 워프 + 선형 컬러램프)을 적용해
    # 격자 경계가 아닌 부드러운 그라디언트로 렌더한다. 예측(qpf)도 같은 목표를
    # 다른 재료로 달성한다(qpf_to_overlay_png 참조 — 그쪽은 이미 색이 입혀진
    # 팔레트 이미지로 오므로 컬러맵을 다시 태울 수 없다).
    grid.warp_to_3857(lcc, merc, nodata=grid.NULL_V, resample="bilinear")
    subprocess.run(["gdaldem", "color-relief", str(merc), str(colormap_path),
                    str(color), "-alpha", "-q"],
                   check=True)
    # 최종 리사이즈도 bilinear로: 기본(nearest)은 1~2px 폭의 경계 그라디언트를
    # 대부분 솎아내 스무딩 효과가 배포 해상도(out_width)에서 사라진다.
    subprocess.run(["gdal_translate", "-q", "-of", "PNG", "-r", "bilinear",
                    "-outsize", str(out_width), "0", str(color), str(out_png)],
                   check=True)
    return grid.bounds_4326(merc)


def _qpf_rgba(png_bytes, workdir):
    """예측 응답 PNG → 배경을 투명화한 (4,H,W) uint8 팔레트 RGBA."""
    raw = workdir / "qpf_raw.png"
    raw.write_bytes(png_bytes)
    a = gdal.Open(str(raw)).ReadAsArray()
    if a is None or a.ndim != 3 or a.shape[0] != 4:
        raise ValueError("expected RGBA qpf image")
    bg = (a[0] == _QPF_BG) & (a[1] == _QPF_BG) & (a[2] == _QPF_BG)
    a[3][bg] = 0                                   # 불투명 회색 배경 → 무강수
    return a


def _write_lcc(bands, cov, path, dtype, nodata=None):
    """(N,H,W) 배열 → 예측 CRS(LCC lat_0=0) 지오레퍼런스 GTiff."""
    n, h, w = bands.shape
    ds = gdal.GetDriverByName("GTiff").Create(str(path), w, h, n, dtype)
    srs = osr.SpatialReference()
    srs.ImportFromProj4(grid.LCC0)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform((cov["sx"], (cov["ex"] - cov["sx"]) / w, 0,
                        cov["sy"], 0, (cov["ey"] - cov["sy"]) / h))
    for i in range(n):
        band = ds.GetRasterBand(i + 1)
        band.WriteArray(bands[i])
        if nodata is not None:
            band.SetNoDataValue(nodata)
    ds.FlushCache()


def _qpf_display_png(rgba, cov, out_png, workdir, out_width):
    """팔레트 RGBA → 부드러운 오버레이 PNG. merc 경로 반환(bounds 계산용).

    **알파 프리멀티플 공간에서 보간한다.** 스트레이트 RGBA를 그대로 보간하면
    완전투명 픽셀의 RGB(예측 응답에선 흰 배경)가 가장자리로 새어나와 강수 경계에
    흰 테를 두른다. RGB에 알파를 미리 곱해두면 투명 픽셀은 기여도 0이 되어
    경계가 색이 아닌 '옅어짐'으로 사라진다 — 합성의 정석.

    원본 팔레트를 그대로 보간하므로 기상청이 준 12색 강도 계조가 살아있다.
    (레벨 0~4 스칼라로 환원해 컬러램프를 다시 태우는 방식도 매끈하긴 하지만,
     같은 레벨 안의 계조가 뭉개져 비구름이 단색 덩어리로 보이고 색조도 바뀐다.)
    팔레트 순수성은 표시용에서 더 이상 필요 없다 — 강도 분류(precip 격자)는
    아래 _qpf_levels 가 near 경로로 따로 뽑는다.
    """
    a = rgba.astype(np.float32)
    al = a[3] / 255.0
    pm = np.stack([a[0] * al, a[1] * al, a[2] * al, a[3]], 0)

    lcc = workdir / "qpf_pm_lcc.tif"
    _write_lcc(pm, cov, lcc, gdal.GDT_Float32)
    merc = workdir / "qpf_3857.tif"
    grid.warp_to_3857(lcc, merc, resample="bilinear")
    big = workdir / "qpf_big.tif"
    # 최종 확대(≈480→2048, 4배)도 bilinear. 여기가 버그의 핵심이었다: 기본
    # nearest는 원본 1px을 4x4 픽셀 블록으로 구워버려, 앱에서 확대하면 레고가 됐다.
    subprocess.run(["gdal_translate", "-q", "-r", "bilinear",
                    "-outsize", str(out_width), "0", str(merc), str(big)],
                   check=True)

    p = gdal.Open(str(big)).ReadAsArray().astype(np.float32)
    alpha = np.clip(p[3], 0, 255)
    # 언프리멀티플: RGB = premul / alpha. alpha≈0인 곳은 색이 정의되지 않으므로
    # 0으로 나누지 않도록 바닥을 깔고, 어차피 투명이라 눈에 띄지 않는다.
    safe = np.maximum(alpha, 1e-3) / 255.0
    rgb = np.clip(np.stack([p[i] / safe for i in range(3)], 0), 0, 255)
    out = np.concatenate([rgb, alpha[None]], 0).astype(np.uint8)

    h, w = out.shape[1], out.shape[2]
    mem = gdal.GetDriverByName("MEM").Create("", w, h, 4, gdal.GDT_Byte)
    for i in range(4):
        mem.GetRasterBand(i + 1).WriteArray(out[i])
    gdal.GetDriverByName("PNG").CreateCopy(str(out_png), mem)
    return merc


def _qpf_levels(rgba, cov, workdir, out_width):
    """팔레트 RGBA → 배포 PNG와 같은 화소격자의 정수 강도레벨 (H,W).

    near 로만 워프·확대해 팔레트 값이 한 톨도 섞이지 않는다 — precip 격자(앱의
    강수 타이밍 배너)는 색이 순수해야 분류가 성립하기 때문. 표시용과 같은
    2048×H 격자에 맞춰야 max-pool 구간이 예전과 동일해져, 앱이 받던 level[]이
    이번 변경으로 한 칸도 바뀌지 않는다(구버전 앱 호환).
    """
    lvl = precip.levels_grid(rgba).astype(np.int16)[None]
    lcc = workdir / "qpf_lvl_lcc.tif"
    _write_lcc(lvl, cov, lcc, gdal.GDT_Int16, nodata=_QPF_NODATA)
    near = workdir / "qpf_lvl_near.tif"
    grid.warp_to_3857(lcc, near, nodata=_QPF_NODATA)
    pooled = workdir / "qpf_lvl_grid.tif"
    subprocess.run(["gdal_translate", "-q", "-r", "near",
                    "-outsize", str(out_width), "0", str(near), str(pooled)],
                   check=True)
    return gdal.Open(str(pooled)).ReadAsArray().astype(np.int16)


def qpf_to_overlay_png(png_bytes, cov, out_png, workdir, out_width=2048):
    """예측 PNG(LCC lat_0=0) → 부드러운 오버레이 PNG. (bounds, levels) 반환.

    표시용(bilinear)과 강도격자용(near)이 래스터를 공유하지 않는다는 게 이 함수의
    요점이다. 예전엔 배포된 표시용 PNG를 precip 이 되읽어 RGB 우세색으로 강도를
    분류했기 때문에, 표시를 부드럽게 만들면 색이 섞여 강도가 조작됐다 — 그래서
    near 를 쓸 수밖에 없었고, 그 결과가 사용자가 본 레고 픽셀이다.

    예측의 원해상도(≈2.7km/px)는 관측(≈0.8km/px)보다 실제로 성기다. 없는 디테일을
    만들 수는 없고, 목표는 그 성긴 값이 하드 4x4 사각형이 아니라 그라디언트로
    보이게 하는 것(과거 프레임과 같은 제품처럼).
    """
    rgba = _qpf_rgba(png_bytes, workdir)
    merc = _qpf_display_png(rgba, cov, out_png, workdir, out_width)
    levels = _qpf_levels(rgba, cov, workdir, out_width)
    return grid.bounds_4326(merc), levels


def opaque_count(png_path) -> int:
    ds = gdal.Open(str(png_path))
    a = ds.ReadAsArray()
    if a is None or a.ndim != 3 or a.shape[0] < 4:
        return 0
    return int((a[3] > 0).sum())
