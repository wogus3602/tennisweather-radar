"""KMA 초단기예보 바람(UUU/VVV, DFS 5km) → 위경도 격자 wind JSON.

DFS georef는 firebase functions kmaGridConverter.ts와 동일 상수(구면
R=6371008.77m). 레이더 격자(+ellps=WGS84)와 다르니 혼용 금지.
"""
import subprocess

import numpy as np
from osgeo import gdal, osr

from . import grid, kma_api

gdal.UseExceptions()

DFS_NX, DFS_NY = 149, 253
DFS_CELL = 5000.0
DFS_ULX, DFS_ULY = -212500.0, 587500.0  # 원점 셀(43,136 1-based)=(E126,N38)
DFS_LCC = ("+proj=lcc +lat_1=30 +lat_2=60 +lat_0=38 +lon_0=126 "
           "+x_0=0 +y_0=0 +R=6371008.77 +units=m +no_defs")
_MISSING = -90.0  # 이하 값은 결측(-99.00)


def parse_dfs_text(body: str) -> np.ndarray:
    """콤마 구분 텍스트 → (253,149) float32 top-down 배열(NaN=결측).

    응답 직렬화는 남→북 행순서(Task 1 Step 4 실데이터 검증으로 확정) —
    top-down으로 뒤집어 반환한다.
    """
    vals = [v for v in body.replace("\n", ",").split(",") if v.strip()]
    if len(vals) != DFS_NX * DFS_NY:
        raise ValueError(f"unexpected count {len(vals)} != {DFS_NX * DFS_NY}")
    arr = np.array(vals, dtype=np.float32).reshape(DFS_NY, DFS_NX)
    arr = np.flipud(arr)  # 남→북 저장 → top-down
    arr[arr < _MISSING] = np.nan
    return arr


def fetch_uv(tmfc: str, tmef: str, key: str):
    """UUU·VVV 두 배열(top-down) 또는 None(미발표/오류)."""
    out = []
    for var in ("UUU", "VVV"):
        url = (f"{kma_api.HOST}/api/typ01/cgi-bin/url/nph-dfs_vsrt_grd"
               f"?tmfc={tmfc}&tmef={tmef}&vars={var}&authKey={key}")
        try:
            body = kma_api._get(url).decode("utf-8", "replace")
            out.append(parse_dfs_text(body))
        except Exception:
            return None
    return out[0], out[1]


def _fill_missing(arr: np.ndarray, iterations: int = 3) -> np.ndarray:
    """결측(NaN)을 유효 4-이웃 평균으로 반복 확장 채움.

    warp(bilinear)가 nodata 인접 셀을 침식시켜 해안 코트가 격자 구멍에
    빠지는 문제를 막는다 — 3회 반복 ≈ 15km 연안 확장(표시용으로 무해).
    내륙 깊은 결측(도메인 밖)은 그대로 NaN 유지.
    """
    out = arr.copy()
    for _ in range(iterations):
        nan = np.isnan(out)
        if not nan.any():
            break
        acc = np.zeros_like(out)
        cnt = np.zeros_like(out)
        for shift in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(out, shift, axis=(0, 1))
            # roll 경계 래핑 오염 방지: 래핑된 가장자리 행/열은 무효 처리
            if shift[0] == 1:
                sh[0, :] = np.nan
            elif shift[0] == -1:
                sh[-1, :] = np.nan
            if shift[1] == 1:
                sh[:, 0] = np.nan
            elif shift[1] == -1:
                sh[:, -1] = np.nan
            valid = ~np.isnan(sh)
            acc[valid] += sh[valid]
            cnt[valid] += 1
        fill = nan & (cnt > 0)
        out[fill] = acc[fill] / cnt[fill]
    return out


def _write_dfs_tiff(arr: np.ndarray, path) -> None:
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(str(path), DFS_NX, DFS_NY, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((DFS_ULX, DFS_CELL, 0, DFS_ULY, 0, -DFS_CELL))
    srs = osr.SpatialReference()
    srs.ImportFromProj4(DFS_LCC)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(np.nan_to_num(arr, nan=-999.0))
    band.SetNoDataValue(-999.0)
    ds.FlushCache()


def build_wind_json(u: np.ndarray, v: np.ndarray, workdir,
                    out_nx: int = 70, out_ny: int = 90) -> dict:
    """U·V top-down 배열 → EPSG:4326 재투영·다운샘플 → 계약 스키마 dict."""
    doc = {}
    for name, arr in (("u", u), ("v", v)):
        arr = _fill_missing(arr)
        lcc = workdir / f"wind_{name}_lcc.tif"
        ll = workdir / f"wind_{name}_4326.tif"
        _write_dfs_tiff(arr, lcc)
        subprocess.run(
            ["gdalwarp", "-overwrite", "-q", "-t_srs", "EPSG:4326",
             "-ts", str(out_nx), str(out_ny), "-r", "bilinear",
             "-srcnodata", "-999", "-dstnodata", "-999", str(lcc), str(ll)],
            check=True)
        ds = gdal.Open(str(ll))
        a = ds.ReadAsArray()
        doc[name] = [None if x < -900 else round(float(x), 1)
                     for x in a.ravel()]
        if "west" not in doc:
            gt = ds.GetGeoTransform()
            doc.update(west=gt[0], north=gt[3],
                       east=gt[0] + gt[1] * ds.RasterXSize,
                       south=gt[3] + gt[5] * ds.RasterYSize,
                       nx=ds.RasterXSize, ny=ds.RasterYSize)
    return doc
