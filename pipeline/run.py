"""수집→렌더→site/ 조립. GitHub Actions와 로컬에서 동일하게 실행."""
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import frames, grid, kma_api, precip, render, wind

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
WORK = ROOT / "work"
COLORMAP = ROOT / "colormap_hsp.txt"
# 5분 × 6 = 30분. 예전엔 24(2시간)였는데, 실측으로 과거 PNG가 장당 평균 732KB라
# 런당 17.2MB를 굽고 타임라인이 과거 67% / 미래 33%로 기울었다 — 사용자가 레이더를
# 여는 이유는 '올 비'인데 슬라이더의 2/3가 지난 비였다. 6장이면 비구름의 방향과
# 속도(예보를 믿게 만드는 바로 그 근거)는 그대로 보이고 '지금'(최신 관측)도 남으면서
# ≈12.9MB를 덜 굽고 미래가 67%가 된다.
PAST_COUNT = 6
NOWCAST_EFS = range(10, 121, 10)  # +10~+120분
QPF_RETRIES = 3


def _fetch_old_frames_json(site_base):
    try:
        with urllib.request.urlopen(f"{site_base}/frames.json", timeout=15) as r:
            return json.load(r)
    except Exception:
        return None


def _download(url, dest: Path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            dest.write_bytes(r.read())
        return True
    except Exception:
        return False


def main() -> int:
    key = os.environ.get("KMA_APIHUB_KEY")
    if not key:
        print("KMA_APIHUB_KEY 필요", file=sys.stderr)
        return 2
    os.environ.setdefault("GDAL_PAM_ENABLED", "NO")
    site_base = os.environ.get(
        "SITE_BASE", "https://wogus3602.github.io/tennisweather-radar")

    shutil.rmtree(SITE, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    (SITE / "frames" / "past").mkdir(parents=True)
    (SITE / "frames" / "nowcast").mkdir(parents=True)
    (SITE / "precip").mkdir(exist_ok=True)
    WORK.mkdir()
    (SITE / ".nojekyll").write_text("")

    old = _fetch_old_frames_json(site_base)

    # ── 과거(HSP 5분) ────────────────────────────────────────────
    downloaded = {}

    def probe(tm):
        raw = kma_api.fetch_hsp(tm, key)
        if raw is not None:
            downloaded[tm] = raw
        return raw is not None

    tms = kma_api.latest_tms(probe, count=PAST_COUNT)
    if not tms:
        print("HSP 최신 발표분을 찾지 못함", file=sys.stderr)
        return 1

    wanted = [f"frames/past/{tm}.png" for tm in tms]
    reuse = frames.reusable_paths(old, wanted)
    past_entries = []
    for tm in tms:
        rel = f"frames/past/{tm}.png"
        dest = SITE / rel
        if rel in reuse and _download(f"{site_base}/{rel}", dest):
            entry = next(f for f in old["frames"] if f.get("path") == rel)
            past_entries.append((tm, rel, entry["bounds"]))
            continue
        raw = downloaded.get(tm) or kma_api.fetch_hsp(tm, key)
        if raw is None:
            print(f"skip past {tm}: 다운로드 실패")
            continue
        try:
            arr = grid.parse_grid(raw)
            bounds = render.render_hsp_png(arr, dest, WORK, COLORMAP)
        except Exception as e:  # 프레임 단위 실패는 건너뛰고 계속
            print(f"skip past {tm}: {e}")
            continue
        past_entries.append((tm, rel, bounds))

    # ── 실황 강도격자(최신 관측 1장) ─────────────────────────────
    # precip 격자가 예측(base+10분~)에만 있어서, 앱은 "지금 비 오나?"를 20분 넘게
    # 낡을 수 있는 예보로 추정해야 했다(이미 그친 비에 "N분 뒤 비 그침"이 뜬 실사용
    # 버그). 관측(HSP)만이 현재의 ground truth이고 이미 받아서 렌더까지 해뒀다.
    #
    # PNG를 재사용(reuse)해 렌더를 건너뛴 프레임이라도 격자는 반드시 만든다 —
    # 원시 바이트는 downloaded 에 남아 있고(최신 tm은 probe가 받아둔다), 없으면
    # 다시 받는다. "PNG를 재사용했으니 격자도 생략"은 곧 '지금'이 사라진다는 뜻.
    #
    # 실패는 비치명적: 격자 없이(past grid 키 없이) 배포한다 — 순수 추가 기능이지
    # 새 중단 조건이 아니다.
    past_grids = {}
    if past_entries:
        tm = max(e[0] for e in past_entries)   # 최신 관측(= "지금")
        try:
            raw = downloaded.get(tm) or kma_api.fetch_hsp(tm, key)
            if raw is None:
                raise RuntimeError("원시 HSP 재수집 실패")
            bounds, levels = render.hsp_levels(grid.parse_grid(raw), WORK)
            grid_rel = f"precip/obs_{tm}.json"   # 예측 격자와 파일명 충돌 방지
            (SITE / grid_rel).write_text(
                json.dumps(precip.build_precip_json(levels, bounds),
                           separators=(",", ":")))
            past_grids[tm] = grid_rel
        except Exception as e:
            print(f"skip obs precip {tm}: {e}")

    # ── 예측(QPF +10~+120) ───────────────────────────────────────
    nowcast_entries = []
    nowcast_grids = {}
    qpf_first = {}

    def qpf_probe(tm):
        got = kma_api.fetch_qpf_once(tm, 10, key)
        if got is not None:
            qpf_first[tm] = got
        return got is not None

    base_tms = kma_api.latest_tms(qpf_probe, step_min=10, count=1, max_back=6)
    if base_tms:
        base = base_tms[0]
        base_dt = datetime.strptime(base + "+0900", "%Y%m%d%H%M%z")
        for ef in NOWCAST_EFS:
            best = None  # (opaque, cov, png)
            remaining = QPF_RETRIES
            preload = qpf_first.get(base) if ef == 10 else None
            if preload is not None:
                remaining -= 1
                cov, png = preload
                tmp = WORK / "probe.png"
                tmp.write_bytes(png)
                try:
                    op = render.opaque_count(tmp)
                except Exception as e:
                    print(f"skip nowcast ef={ef} attempt: 응답 검사 실패 {e}")
                else:
                    best = (op, cov, png)
            for _ in range(remaining):
                if best is not None and best[0] > 0:
                    break
                got = kma_api.fetch_qpf_once(base, ef, key)
                if got is None:
                    continue
                cov, png = got
                tmp = WORK / "probe.png"
                tmp.write_bytes(png)
                try:
                    op = render.opaque_count(tmp)
                except Exception as e:
                    print(f"skip nowcast ef={ef} attempt: 응답 검사 실패 {e}")
                    continue
                if best is None or op > best[0]:
                    best = (op, cov, png)
                if op > 0:
                    break  # 간헐 빈 응답 우회 — 내용 있으면 즉시 채택
            if best is None:
                print(f"skip nowcast ef={ef}: 응답 없음")
                continue
            _, cov, png = best
            valid_tm = (base_dt + timedelta(minutes=ef)).strftime("%Y%m%d%H%M")
            rel = f"frames/nowcast/{valid_tm}.png"
            try:
                bounds, levels = render.qpf_to_overlay_png(
                    png, cov, SITE / rel, WORK)
            except Exception as e:
                print(f"skip nowcast ef={ef}: {e}")
                continue
            nowcast_entries.append((valid_tm, rel, bounds))
            # 강수 타이밍용 강도 격자(코트 지점 샘플링). 실패는 격자만 생략.
            # 배포된 표시용 PNG를 되읽지 않는다 — 스무딩된 색은 강도로 분류할 수
            # 없다. 렌더가 워프 전 순수 팔레트에서 뽑아둔 레벨을 그대로 쓴다.
            try:
                grid_doc = precip.build_precip_json(levels, bounds)
                grid_rel = f"precip/{valid_tm}.json"
                (SITE / grid_rel).write_text(
                    json.dumps(grid_doc, separators=(",", ":")))
                nowcast_grids[valid_tm] = grid_rel
            except Exception as e:
                print(f"skip precip {valid_tm}: {e}")
    else:
        print("QPF 발표분 없음 — 예측 프레임 생략")

    # ── 바람(U·V, 시간당 1회 + Pages 재사용) ─────────────────────
    def _wind_file_ok(p) -> bool:
        """재사용 다운로드한 wind 파일이 유효 데이터를 갖는지(전부-null 거부)."""
        try:
            doc = json.loads(p.read_text())
            return (any(x is not None for x in doc.get("u", []))
                    and any(x is not None for x in doc.get("v", [])))
        except Exception:
            return False
    (SITE / "wind").mkdir(exist_ok=True)
    wind_entries = []
    now = datetime.now(kma_api.KST)
    wind_hours = [
        (now.replace(minute=0, second=0, microsecond=0)
         + timedelta(hours=h)).strftime("%Y%m%d%H%M")
        for h in range(-2, 7)
    ]
    refresh = frames.wind_refresh_needed(old, frames.tm_to_iso(wind_hours[-1]))
    refresh = refresh or os.environ.get("WIND_FORCE_REFRESH") == "1"
    old_wind = {w.get("path") for w in (old or {}).get("wind", [])
                if isinstance(w, dict)}
    for vt in wind_hours:
        rel = f"wind/{vt}.json"
        dest = SITE / rel
        is_future = vt > now.strftime("%Y%m%d%H%M")
        # 과거·현재 시각은 재사용 우선(예보 갱신 의미 없음), 미래는
        # refresh 런에서 재수집, 아닌 런은 재사용.
        if rel in old_wind and (not refresh or not is_future) \
                and _download(f"{site_base}/{rel}", dest) and _wind_file_ok(dest):
            wind_entries.append((vt, rel))
            continue
        if not refresh and rel not in old_wind:
            continue  # 비갱신 런은 신규 수집 안 함
        base_dt = datetime.strptime(vt + "+0900", "%Y%m%d%H%M%z")
        got = None
        for back in range(1, 4):  # (VT-1h)30부터 과거로 최대 3개 후보
            tmfc_dt = base_dt - timedelta(hours=back)
            if tmfc_dt > datetime.now(kma_api.KST):
                continue  # 미발표(미래) 발표시각 제외
            tmfc = tmfc_dt.strftime("%Y%m%d%H") + "30"
            cand = wind.fetch_uv(tmfc, vt, key)
            if cand is not None and wind.coverage(cand[0]) > 0.01 and wind.coverage(cand[1]) > 0.01:
                got = cand
                break
        if got is None:
            print(f"skip wind {vt}: 수집 실패(발표 지연/무자료)")
            continue
        try:
            doc = wind.build_wind_json(got[0], got[1], WORK)
            dest.write_text(json.dumps(doc, separators=(",", ":")))
        except Exception as e:
            print(f"skip wind {vt}: 쓰기 실패 {e}")
            continue
        wind_entries.append((vt, rel))

    if not past_entries:
        print("과거 프레임 0개 — 배포 중단", file=sys.stderr)
        return 1

    for p in SITE.rglob("*.aux.xml"):  # gdal PAM 사이드카 잔재 안전망
        p.unlink()

    generated = datetime.now(kma_api.KST).isoformat(timespec="seconds")
    doc = frames.build_frames_json(past_entries, nowcast_entries, generated,
                                   wind_entries=wind_entries,
                                   nowcast_grids=nowcast_grids,
                                   past_grids=past_grids)
    (SITE / "frames.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1))
    print(f"완료: past {len(past_entries)}, nowcast {len(nowcast_entries)}, "
          f"wind {len(wind_entries)}, precip {len(nowcast_grids)}, "
          f"obs precip {len(past_grids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
