"""예측 QPF 오버레이 PNG(EPSG:3857 투명배경) → 강수강도 버킷 격자.

near 워프라 팔레트가 이산(12색)이므로 RGB 우세색으로 버킷을 결정한다
(정확 색매칭 테이블 없이 결정론적). 0=무강수, 1=약, 2=보통, 3=강, 4=매우강.
"""
import numpy as np
from osgeo import gdal

gdal.UseExceptions()

OUT_NX, OUT_NY = 140, 180


def level_of(r: int, g: int, b: int, a: int) -> int:
    if a == 0:
        return 0
    r, g, b = int(r), int(g), int(b)
    if r >= 128 and g < 128 and b < 128:
        return 4          # 빨강 계열(매우강)
    if r >= 128 and b >= 128:
        return 4          # 보라 계열(매우강)
    if r >= 128 and g >= 128:
        return 3          # 노랑·주황(강)
    if g >= b and g > r:
        return 2          # 초록(보통)
    return 1              # 파랑 계열(약)


def _levels_grid(rgba: np.ndarray) -> np.ndarray:
    """(4,H,W) uint8 → (H,W) int8 레벨. 벡터화."""
    r, g, b, a = rgba[0], rgba[1], rgba[2], rgba[3]
    lvl = np.ones(r.shape, dtype=np.int16)          # 기본 1(파랑)
    green = (g >= b) & (g > r)
    lvl[green] = 2
    strong = (r >= 128) & (g >= 128)
    lvl[strong] = 3
    very = ((r >= 128) & (g < 128) & (b < 128)) | ((r >= 128) & (b >= 128))
    lvl[very] = 4
    lvl[a == 0] = 0
    return lvl


def build_precip_json(rgba_png_path, cov_bounds, out_nx=OUT_NX, out_ny=OUT_NY):
    ds = gdal.Open(str(rgba_png_path))
    arr = ds.ReadAsArray()
    if arr is None or arr.ndim != 3 or arr.shape[0] < 4:
        raise ValueError("expected RGBA png")
    lvl = _levels_grid(arr)                          # (H,W)
    h, w = lvl.shape
    out = np.zeros((out_ny, out_nx), dtype=np.int16)
    # max-pool: 출력 셀 = 대응 입력영역의 최대 레벨(소나기 피크 보존)
    ys = (np.linspace(0, h, out_ny + 1)).astype(int)
    xs = (np.linspace(0, w, out_nx + 1)).astype(int)
    for j in range(out_ny):
        y0, y1 = ys[j], max(ys[j] + 1, ys[j + 1])
        row = lvl[y0:y1]
        for i in range(out_nx):
            x0, x1 = xs[i], max(xs[i] + 1, xs[i + 1])
            out[j, i] = int(row[:, x0:x1].max())
    return {"west": cov_bounds["west"], "south": cov_bounds["south"],
            "east": cov_bounds["east"], "north": cov_bounds["north"],
            "nx": out_nx, "ny": out_ny, "level": [int(v) for v in out.ravel()]}
