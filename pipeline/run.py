"""수집→렌더→site/ 조립. GitHub Actions와 로컬에서 동일하게 실행."""
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import frames, grid, kma_api, render, wind

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
WORK = ROOT / "work"
COLORMAP = ROOT / "colormap_hsp.txt"
PAST_COUNT = 24          # 5분 × 2시간
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

    # ── 예측(QPF +10~+120) ───────────────────────────────────────
    nowcast_entries = []
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
                bounds = render.qpf_to_overlay_png(png, cov, SITE / rel, WORK)
            except Exception as e:
                print(f"skip nowcast ef={ef}: {e}")
                continue
            nowcast_entries.append((valid_tm, rel, bounds))
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
                                   wind_entries=wind_entries)
    (SITE / "frames.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1))
    print(f"완료: past {len(past_entries)}, nowcast {len(nowcast_entries)}, "
          f"wind {len(wind_entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
