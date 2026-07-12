"""기상청 API허브 클라이언트 — 실황(HSP) 바이너리·초단기 예측(4.4) 이미지."""
import gzip
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HOST = "https://apihub.kma.go.kr"

# 4.4(nph-qpf_ana_imgp) 고정 파라미터 — PoC에서 map=R(배경 없음)·범례/지명
# off 조합으로 검증. STARTX~ENDY는 QPF 레이어 전체 도메인(LCC lat_0=0).
_QPF_FIXED = dict(
    PROJ="LCC", cmp="HSR", obs="qpf", qcd="EXT", itv="10", tm_mode="m10",
    data0="RCM", level="C", map="R", dtm="m0", zoom_level="0", zoom_rate="2",
    zoom_x="0000000", zoom_y="0000000", auto_man="1", mode="I", umove="10",
    fmove="2", dmove="180", bmove="10", winnum="0", rand="10", an_frn="1",
    an_itv="1", river="off", road="off", city="off", gis_auto="off",
    stnname="off", ctrl="0", dataDtlCd="rdr_rdr_qpf_ana1_0", data1="r01",
    data2="rdr_qpf_ana1", data3="0", overlay="spr", color="C4", effect="N",
    height="320", eva="1", option="1", grid="2", size="320",
    STARTX="-436145", STARTY="4816073", ENDX="580166", ENDY="3804254",
    ZOOMLVL="11", selWs="kh", legend="0", aws="0", qpf="M",
)


def _get(url: str, timeout: int = 60) -> bytes:
    """apihub GET. KMA_PROXY_BASE가 설정되면 프록시 경유.

    GitHub Actions 러너(해외 IP)는 apihub에 TCP 연결이 차단되므로, 서울
    리전 Firebase 함수(kmaRadarProxy)를 시크릿 헤더 인증으로 경유한다.
    로컬(국내망)에서는 env 미설정으로 직접 호출."""
    proxy_base = os.environ.get("KMA_PROXY_BASE")
    if proxy_base and url.startswith(HOST):
        req = urllib.request.Request(
            f"{proxy_base}?url=" + urllib.parse.quote(url, safe=""),
            headers={"x-radar-proxy-key":
                     os.environ.get("KMA_PROXY_SECRET", "")})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def latest_tms(probe, now=None, step_min=5, count=24, max_back=12):
    """지금(KST)부터 step_min씩 과거로 probe가 참인 첫 tm을 찾고,
    거기서 과거 방향 count개의 tm 목록(최신순)을 돌려준다."""
    now = now or datetime.now(KST)
    t = now.replace(minute=now.minute - now.minute % step_min,
                    second=0, microsecond=0)
    for _ in range(max_back):
        if probe(t.strftime("%Y%m%d%H%M")):
            return [(t - timedelta(minutes=step_min * k)).strftime("%Y%m%d%H%M")
                    for k in range(count)]
        t -= timedelta(minutes=step_min)
    return []


def fetch_hsp(tm: str, key: str):
    """HSP 바이너리(gunzip 후). 미발표·오류·비바이너리 응답은 None."""
    url = (f"{HOST}/api/typ04/url/rdr_cmp_file.php"
           f"?tm={tm}&data=bin&cmp=hsp&authKey={key}")
    try:
        body = _get(url)
    except Exception:
        return None
    if body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except Exception:
            return None
    return body if len(body) > 1_000_000 else None


def fetch_qpf_once(tm: str, ef: int, key: str):
    """예측 이미지 1회 호출 → (coverage, png_bytes) 또는 None.

    coverage = {"sx","sy","ex","ey"} (LCC lat_0=0, start=좌상단).
    간헐 빈 이미지는 여기서 판단하지 않는다(호출부에서 재시도·채택).
    미발표·네트워크 실패는 조용히 None. 응답 스키마 불일치는 파라미터/명세
    버그 신호라 stderr에 남기고 None — 운영에서 '데이터 없음'과 구분한다.
    """
    p = dict(_QPF_FIXED, tm=tm, tm_st=tm, tm_ed=tm, tm2=tm, ef=str(ef),
             authKey=key)
    url = f"{HOST}/api/typ03/cgi/rdr/nph-qpf_ana_imgp?" + urllib.parse.urlencode(p)
    try:
        body = _get(url)
    except Exception:
        return None
    try:
        d = json.loads(body)
        r = d["data"]["result"]
        if d.get("meta", {}).get("errCd") != "000" or not r.get("url"):
            return None
        cov = {"sx": float(r["imageCoverageStartProjX"]),
               "sy": float(r["imageCoverageStartProjY"]),
               "ex": float(r["imageCoverageEndProjX"]),
               "ey": float(r["imageCoverageEndProjY"])}
    except Exception as e:
        print(f"qpf 응답 스키마 예상 밖(tm={tm} ef={ef}): {e!r}", file=sys.stderr)
        return None
    try:
        png = _get(HOST + r["url"])
    except Exception:
        return None
    return cov, png
