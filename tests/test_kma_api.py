import contextlib
import os
import io
import json
import unittest
from datetime import datetime

from pipeline import kma_api


class LatestTmsTest(unittest.TestCase):
    NOW = datetime(2026, 7, 11, 22, 33, tzinfo=kma_api.KST)

    def test_walks_back_to_first_published(self):
        published = {"202607112220"}  # 22:30/22:25는 미발표 상황
        tms = kma_api.latest_tms(lambda tm: tm in published or tm < "202607112220",
                                 now=self.NOW, count=3)
        self.assertEqual(tms, ["202607112220", "202607112215", "202607112210"])

    def test_returns_empty_when_nothing_published(self):
        tms = kma_api.latest_tms(lambda tm: False, now=self.NOW, count=3,
                                 max_back=4)
        self.assertEqual(tms, [])

    def test_step_10min(self):
        tms = kma_api.latest_tms(lambda tm: True, now=self.NOW, count=2,
                                 step_min=10)
        self.assertEqual(tms, ["202607112230", "202607112220"])


class FetchQpfOnceTest(unittest.TestCase):
    def _patch_get(self, responses):
        """responses: url 순서대로 돌려줄 bytes 목록. 호출 url도 기록."""
        calls = []

        def fake_get(url, timeout=60):
            calls.append(url)
            return responses[len(calls) - 1]

        self._orig = kma_api._get
        kma_api._get = fake_get
        self.addCleanup(lambda: setattr(kma_api, "_get", self._orig))
        return calls

    def test_happy_path_returns_coverage_and_png(self):
        body = json.dumps({
            "meta": {"errCd": "000", "msg": ""},
            "data": {"result": {
                "url": "/data/img/x.png",
                "imageCoverageStartProjX": "-386015.5",
                "imageCoverageStartProjY": "4821054.0",
                "imageCoverageEndProjX": "585174.375",
                "imageCoverageEndProjY": "3799270.5",
            }}}).encode()
        calls = self._patch_get([body, b"PNGBYTES"])
        got = kma_api.fetch_qpf_once("202607111800", 60, "KEY")
        self.assertIsNotNone(got)
        cov, png = got
        self.assertEqual(png, b"PNGBYTES")
        self.assertEqual(cov, {"sx": -386015.5, "sy": 4821054.0,
                               "ex": 585174.375, "ey": 3799270.5})
        self.assertIn("nph-qpf_ana_imgp", calls[0])
        self.assertIn("ef=60", calls[0])
        self.assertIn("tm=202607111800", calls[0])
        self.assertTrue(calls[1].endswith("/data/img/x.png"))

    def test_errcd_not_000_returns_none(self):
        body = json.dumps({"meta": {"errCd": "100"}, "data": {"result": {}}}).encode()
        self._patch_get([body])
        self.assertIsNone(kma_api.fetch_qpf_once("202607111800", 60, "KEY"))

    def test_schema_mismatch_returns_none_and_logs(self):
        body = json.dumps({
            "meta": {"errCd": "000"},
            "data": {"result": {"url": "/data/img/x.png"}}}).encode()  # coverage 필드 없음
        self._patch_get([body])
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.assertIsNone(kma_api.fetch_qpf_once("202607111800", 60, "KEY"))
        self.assertIn("스키마", buf.getvalue())


if __name__ == "__main__":
    unittest.main()


class ProxyRoutingTest(unittest.TestCase):
    def setUp(self):
        self._calls = []
        self._orig = kma_api.urllib.request.urlopen
        test = self

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"BYTES"

        def fake_urlopen(url_or_req, timeout=60):
            test._calls.append(url_or_req)
            return FakeResp()

        kma_api.urllib.request.urlopen = fake_urlopen
        self.addCleanup(
            lambda: setattr(kma_api.urllib.request, "urlopen", self._orig))
        for k in ("KMA_PROXY_BASE", "KMA_PROXY_SECRET"):
            os.environ.pop(k, None)
        self.addCleanup(
            lambda: [os.environ.pop(k, None)
                     for k in ("KMA_PROXY_BASE", "KMA_PROXY_SECRET")])

    def test_direct_without_proxy_env(self):
        out = kma_api._get("https://apihub.kma.go.kr/x?a=1")
        self.assertEqual(out, b"BYTES")
        self.assertEqual(self._calls[0], "https://apihub.kma.go.kr/x?a=1")

    def test_routes_via_proxy_with_secret_header(self):
        os.environ["KMA_PROXY_BASE"] = "https://proxy.example/kmaRadarProxy"
        os.environ["KMA_PROXY_SECRET"] = "s3cret"
        kma_api._get("https://apihub.kma.go.kr/x?a=1&b=2")
        req = self._calls[0]
        self.assertEqual(
            req.full_url,
            "https://proxy.example/kmaRadarProxy?url="
            "https%3A%2F%2Fapihub.kma.go.kr%2Fx%3Fa%3D1%26b%3D2")
        self.assertEqual(req.get_header("X-radar-proxy-key"), "s3cret")

    def test_non_apihub_url_stays_direct(self):
        os.environ["KMA_PROXY_BASE"] = "https://proxy.example/p"
        kma_api._get("https://wogus3602.github.io/frames.json")
        self.assertEqual(self._calls[0],
                         "https://wogus3602.github.io/frames.json")
