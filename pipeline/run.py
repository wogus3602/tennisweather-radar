"""수집→렌더→site/ 조립. GitHub Actions와 로컬에서 동일하게 실행."""
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import frames, grid, kma_api, render

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
    base_tms = kma_api.latest_tms(
        lambda tm: kma_api.fetch_qpf_once(tm, 10, key) is not None,
        step_min=10, count=1, max_back=6)
    if base_tms:
        base = base_tms[0]
        base_dt = datetime.strptime(base + "+0900", "%Y%m%d%H%M%z")
        for ef in NOWCAST_EFS:
            best = None  # (opaque, cov, png)
            for _ in range(QPF_RETRIES):
                got = kma_api.fetch_qpf_once(base, ef, key)
                if got is None:
                    continue
                cov, png = got
                tmp = WORK / "probe.png"
                tmp.write_bytes(png)
                op = render.opaque_count(tmp)
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

    if not past_entries:
        print("과거 프레임 0개 — 배포 중단", file=sys.stderr)
        return 1

    generated = datetime.now(kma_api.KST).isoformat(timespec="seconds")
    doc = frames.build_frames_json(past_entries, nowcast_entries, generated)
    (SITE / "frames.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1))
    print(f"완료: past {len(past_entries)}, nowcast {len(nowcast_entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
