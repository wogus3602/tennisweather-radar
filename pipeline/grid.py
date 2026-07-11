"""KMA 레이더 합성 격자(2305x2881, 500m, LCC) 파싱·georef·재투영.

좌표 상수는 2026-07-11 PoC에서 백령도 관측권 원피팅으로 검증된 값 — 임의
변경 금지(스펙 docs/superpowers/specs/2026-07-11-kma-radar-pipeline-design.md).
"""
import math
import subprocess

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()
osr.UseExceptions()

NX, NY = 2305, 2881
HEADER_BYTES = 1024
EXPECTED_SIZE = HEADER_BYTES + 2 * NX * NY
CELL = 500.0
ULX, ULY = -559750.0, 600750.0  # 셀(1120,1680 1-based bottom-up)=LCC(0,0)
LCC38 = ("+proj=lcc +lat_1=30 +lat_2=60 +lat_0=38 +lon_0=126 "
         "+x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs")
LCC0 = LCC38.replace("+lat_0=38", "+lat_0=0")  # 예측(4.4) 이미지 CRS
NULL_V, NORAIN_V = -30000, -25000


def parse_grid(raw: bytes) -> np.ndarray:
    """바이너리(gunzip 후) → top-down int16 배열. 크기 불일치는 ValueError."""
    if len(raw) != EXPECTED_SIZE:
        raise ValueError(f"unexpected size {len(raw)} != {EXPECTED_SIZE}")
    arr = np.frombuffer(raw, dtype="<i2", offset=HEADER_BYTES).reshape(NY, NX)
    return np.flipud(arr)  # 파일은 남→북 행순서


def write_lcc_tiff(arr: np.ndarray, path) -> None:
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(str(path), NX, NY, 1, gdal.GDT_Int16,
                    options=["COMPRESS=DEFLATE"])
    ds.SetGeoTransform((ULX, CELL, 0, ULY, 0, -CELL))
    srs = osr.SpatialReference()
    srs.ImportFromProj4(LCC38)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.SetNoDataValue(NULL_V)
    ds.FlushCache()


def warp_to_3857(src, dst, nodata=None) -> None:
    cmd = ["gdalwarp", "-overwrite", "-q", "-t_srs", "EPSG:3857", "-r", "near"]
    if nodata is not None:
        cmd += ["-srcnodata", str(nodata), "-dstnodata", str(nodata)]
    cmd += [str(src), str(dst)]
    subprocess.run(cmd, check=True)


def bounds_4326(tif) -> dict:
    """EPSG:3857 래스터의 extent를 위경도 사각형으로."""
    ds = gdal.Open(str(tif))
    gt = ds.GetGeoTransform()
    xs = [gt[0], gt[0] + gt[1] * ds.RasterXSize]
    ys = [gt[3], gt[3] + gt[5] * ds.RasterYSize]

    def lon(x):
        return x / 6378137.0 * 180.0 / math.pi

    def lat(y):
        return (2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2) \
            * 180.0 / math.pi

    return {"west": lon(min(xs)), "south": lat(min(ys)),
            "east": lon(max(xs)), "north": lat(max(ys))}
