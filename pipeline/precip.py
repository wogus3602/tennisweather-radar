"""예측 QPF 팔레트 → 강수강도 버킷 격자(앱의 강수 타이밍 배너 입력).

팔레트가 이산(12색)이므로 RGB 우세색으로 버킷을 결정한다(정확 색매칭 테이블
없이 결정론적). 0=무강수, 1=약, 2=보통, 3=강, 4=매우강.

주의 — 이 분류는 '순수 팔레트' 위에서만 성립한다: 색을 보간하면 파랑↔초록 경계가
섞여 (r>=128 & g>=128) 같은 엉뚱한 가지를 타 강도가 조작된다. 그래서 표시용으로
부드럽게 만든 래스터를 여기에 먹이면 안 되고, render 가 워프 이전 원해상도에서
레벨을 뽑아 넘긴다(표시용 bilinear / 격자용 near 로 소비자를 분리).
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


def levels_grid(rgba: np.ndarray) -> np.ndarray:
    """(4,H,W) uint8 순수 팔레트 → (H,W) int16 레벨. 벡터화."""
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


def _levels_of_png(path) -> np.ndarray:
    ds = gdal.Open(str(path))
    arr = ds.ReadAsArray()
    if arr is None or arr.ndim != 3 or arr.shape[0] < 4:
        raise ValueError("expected RGBA png")
    return levels_grid(arr)


def build_precip_json(src, cov_bounds, out_nx=OUT_NX, out_ny=OUT_NY):
    """src: 레벨 배열(H,W) 또는 순수 팔레트 RGBA PNG 경로 → 격자 문서.

    스키마(west/south/east/north/nx/ny/level)는 출시된 앱이 파싱하므로 불변 —
    필드를 빼거나 이름을 바꾸면 구버전 파서가 throw 한다.
    """
    lvl = src if isinstance(src, np.ndarray) else _levels_of_png(src)
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
