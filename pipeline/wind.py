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
