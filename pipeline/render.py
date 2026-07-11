"""프레임 렌더링 — 실황(int16→컬러 PNG), 예측(배경 투명화→재투영 PNG)."""
import subprocess

from osgeo import gdal, osr

from . import grid

gdal.UseExceptions()

_QPF_BG = 250  # 예측 이미지의 불투명 배경 회색값(R=G=B=250)


def render_hsp_png(arr, out_png, workdir, colormap_path, out_width=1024):
    """top-down HSP 배열 → 재투영·컬러맵·투명 PNG. bounds(4326) 반환."""
    lcc = workdir / "hsp_lcc.tif"
    merc = workdir / "hsp_3857.tif"
    color = workdir / "hsp_color.tif"
    grid.write_lcc_tiff(arr, lcc)
    grid.warp_to_3857(lcc, merc, nodata=grid.NULL_V)
    subprocess.run(["gdaldem", "color-relief", str(merc), str(colormap_path),
                    str(color), "-alpha", "-nearest_color_entry", "-q"],
                   check=True)
    subprocess.run(["gdal_translate", "-q", "-of", "PNG",
                    "-outsize", str(out_width), "0", str(color), str(out_png)],
                   check=True)
    return grid.bounds_4326(merc)


def qpf_to_overlay_png(png_bytes, cov, out_png, workdir, out_width=1024):
    """예측 PNG(LCC lat_0=0) → 배경 투명화 + 3857 재투영 PNG. bounds 반환."""
    raw = workdir / "qpf_raw.png"
    raw.write_bytes(png_bytes)
    src = gdal.Open(str(raw))
    a = src.ReadAsArray()
    if a is None or a.ndim != 3 or a.shape[0] != 4:
        raise ValueError("expected RGBA qpf image")
    bg = (a[0] == _QPF_BG) & (a[1] == _QPF_BG) & (a[2] == _QPF_BG)
    a[3][bg] = 0

    lcc = workdir / "qpf_lcc.tif"
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(str(lcc), a.shape[2], a.shape[1], 4, gdal.GDT_Byte)
    srs = osr.SpatialReference()
    srs.ImportFromProj4(grid.LCC0)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform((cov["sx"], (cov["ex"] - cov["sx"]) / a.shape[2], 0,
                        cov["sy"], 0, (cov["ey"] - cov["sy"]) / a.shape[1]))
    for b in range(4):
        ds.GetRasterBand(b + 1).WriteArray(a[b])
    ds.FlushCache()
    ds = None

    merc = workdir / "qpf_3857.tif"
    grid.warp_to_3857(lcc, merc)
    subprocess.run(["gdal_translate", "-q", "-of", "PNG",
                    "-outsize", str(out_width), "0", str(merc), str(out_png)],
                   check=True)
    return grid.bounds_4326(merc)


def opaque_count(png_path) -> int:
    ds = gdal.Open(str(png_path))
    a = ds.ReadAsArray()
    if a is None or a.ndim != 3 or a.shape[0] < 4:
        return 0
    return int((a[3] > 0).sum())
