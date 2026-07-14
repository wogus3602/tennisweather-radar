"""run.main() 조립 — 실황 격자가 '재사용 런'에서도 살아남고, 실패해도 안 죽는다.

네트워크(KMA·Pages)는 전부 스텁하되 렌더는 진짜로 돌린다 — 격자가 실제로
만들어지는지가 요점이므로. PAST_COUNT만 2로 낮춰 렌더 비용을 줄인다(상수 값 자체는
아래 ConstantTest 가 따로 못 박는다).
"""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from pipeline import grid, kma_api, render, run, wind

SITE_BASE = "http://pages.test"


def fake_hsp_raw():
    """HSP 바이너리 모사(파일은 남→북 행순서, 값 = mm/h × 100)."""
    arr = np.full((grid.NY, grid.NX), grid.NORAIN_V, dtype="<i2")
    arr[1400:1500, 1100:1200] = 500     # 5.0mm/h → level 2
    return bytes(grid.HEADER_BYTES) + arr.tobytes()


def past_tms(doc):
    """frames.json → 과거 프레임 tm 목록(최신순, latest_tms 규약과 동일)."""
    past = sorted((f for f in doc["frames"] if f["kind"] == "past"),
                  key=lambda f: f["time"], reverse=True)
    return [f["path"].rsplit("/", 1)[-1][:-len(".png")] for f in past]


class RunTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.raw = fake_hsp_raw()
        # QPF·바람은 '미발표'로 두어 과거 경로만 남긴다(테스트 대상 아님).
        for p in [
            mock.patch.object(run, "SITE", self.td / "site"),
            mock.patch.object(run, "WORK", self.td / "work"),
            mock.patch.object(run, "PAST_COUNT", 2),
            mock.patch.object(kma_api, "fetch_hsp", lambda tm, key: self.raw),
            mock.patch.object(kma_api, "fetch_qpf_once",
                              lambda tm, ef, key: None),
            mock.patch.object(wind, "fetch_uv", lambda *a, **k: None),
            mock.patch.dict("os.environ", {"KMA_APIHUB_KEY": "test",
                                           "SITE_BASE": SITE_BASE}),
        ]:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, old=None, downloader=None):
        """old: 이전 배포 frames.json / downloader: Pages 재다운로드 스텁.

        run.main()의 진행 로그는 삼켰다가 실패했을 때만 보여준다 — 안 그러면
        `unittest discover` 출력이 skip 메시지로 뒤덮여 요약이 안 보인다.
        """
        log = io.StringIO()
        with mock.patch.object(run, "_fetch_old_frames_json",
                               lambda base: old), \
             mock.patch.object(run, "_download",
                               downloader or (lambda url, dest: False)), \
             contextlib.redirect_stdout(log):
            code = run.main()
        self.assertEqual(code, 0, f"run.main() 실패:\n{log.getvalue()}")
        return json.loads((run.SITE / "frames.json").read_text())

    def _latest_past(self, doc):
        past = [f for f in doc["frames"] if f["kind"] == "past"]
        self.assertTrue(past)
        return max(past, key=lambda f: f["time"])

    def test_fresh_run_emits_observation_grid(self):
        doc = self._run()
        latest = self._latest_past(doc)
        self.assertTrue(latest["grid"].startswith("precip/obs_"))
        # 나머지 과거 프레임엔 grid 키 자체가 없다(최신 1장만 — 순수 추가)
        self.assertTrue(all("grid" not in f for f in doc["frames"]
                            if f["kind"] == "past"
                            and f["path"] != latest["path"]))

        g = json.loads((run.SITE / latest["grid"]).read_text())
        self.assertEqual(set(g), {"west", "south", "east", "north",
                                  "nx", "ny", "level"})   # 예측 격자와 동일 스키마
        self.assertEqual(len(g["level"]), g["nx"] * g["ny"])
        self.assertTrue(all(0 <= v <= 4 for v in g["level"]))
        self.assertIn(2, g["level"])        # 5mm/h 블록이 실제로 실렸는가

    def test_grid_survives_png_reuse(self):
        """PNG를 이전 배포에서 재사용해도 실황 격자는 반드시 나온다.

        run.py는 과거 PNG를 재렌더하지 않고 Pages에서 내려받아 재사용한다.
        "PNG를 재사용했으니 격자도 생략"하면 앱에서 '지금'이 통째로 사라진다 —
        이 파이프라인에서 제일 밟기 쉬운 지뢰라 못 박아 둔다.

        latest_tms 까지 스텁해 probe 가 안 돌게 만든다: 그래야 downloaded 캐시가
        비어 '원시 HSP를 다시 받아서라도 격자를 만든다'는 폴백이 실제로 시험된다.
        """
        first = self._run()                    # 1) 신선한 런 = '이전 배포'
        prev = self.td / "prev"
        shutil.copytree(run.SITE, prev)

        def download(url, dest):               # Pages 재다운로드 모사
            src = prev / url[len(SITE_BASE) + 1:]
            if not src.exists():
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            return True

        tms = past_tms(first)
        # step_min=5(과거 HSP) 호출만 가로챈다 — QPF 발표분 탐색(step_min=10)까지
        # 이걸로 답하면 엉뚱한 예측 프레임을 굽는다.
        with mock.patch.object(
                kma_api, "latest_tms",
                lambda probe, now=None, step_min=5, **k:
                    tms if step_min == 5 else []), \
             mock.patch.object(render, "render_hsp_png") as rendered:
            doc = self._run(old=first, downloader=download)

        # 한 장도 재렌더하지 않았다 = 진짜 재사용 런이다(안 그러면 이 테스트가
        # 재사용을 시험하는 척만 하게 된다)
        self.assertEqual(rendered.call_count, 0)
        self.assertEqual([f["path"] for f in doc["frames"]],
                         [f["path"] for f in first["frames"]])
        latest = self._latest_past(doc)
        self.assertEqual(latest["grid"], f"precip/obs_{tms[0]}.json")
        self.assertTrue((run.SITE / latest["grid"]).exists())

    def test_observation_grid_failure_is_non_fatal(self):
        """격자 실패는 '있으면 좋은 것'의 실패 — 배포를 중단시키면 안 된다."""
        with mock.patch("pipeline.render.hsp_levels",
                        side_effect=RuntimeError("boom")):
            doc = self._run()
        self.assertNotIn("grid", self._latest_past(doc))
        self.assertFalse(list((run.SITE / "precip").glob("obs_*.json")))


class ConstantTest(unittest.TestCase):
    def test_past_count_is_30min(self):
        """과거 30분(5분×6). 늘리면 배포 용량과 '과거로 기운 슬라이더'가 돌아온다."""
        self.assertEqual(run.PAST_COUNT, 6)


if __name__ == "__main__":
    unittest.main()
