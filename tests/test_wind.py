import tempfile
import unittest
from pathlib import Path

import numpy as np

from pipeline import wind


def synthetic_body(u0=3.0):
    """동풍 u0 m/s 균일 + 남서 모서리 결측 블록의 DFS 텍스트."""
    arr = np.full((wind.DFS_NY, wind.DFS_NX), u0, dtype=np.float32)
    arr[:20, :20] = -99.0  # top-down 배열의 북서 → 저장 방향은 구현이 처리
    vals = [f"{v:7.2f}" for v in arr[::-1].ravel()]  # 남→북 행순서로 직렬화
    lines = [", ".join(vals[i:i + 21]) for i in range(0, len(vals), 21)]
    return ",\n".join(lines)


class WindTest(unittest.TestCase):
    def test_parse_shape_and_nan(self):
        out = wind.parse_dfs_text(synthetic_body())
        self.assertEqual(out.shape, (wind.DFS_NY, wind.DFS_NX))
        self.assertTrue(np.isnan(out[:20, :20]).all())  # top-down 북서에 결측
        self.assertAlmostEqual(float(out[-1, -1]), 3.0, places=2)

    def test_parse_rejects_wrong_count(self):
        with self.assertRaises(ValueError):
            wind.parse_dfs_text("1.0, 2.0, 3.0")

    def test_build_wind_json_schema_and_bounds(self):
        u = wind.parse_dfs_text(synthetic_body(3.0))
        v = wind.parse_dfs_text(synthetic_body(-1.5))
        with tempfile.TemporaryDirectory() as td:
            doc = wind.build_wind_json(u, v, Path(td))
        self.assertEqual((doc["nx"], doc["ny"]), (70, 90))
        self.assertEqual(len(doc["u"]), 70 * 90)
        self.assertEqual(len(doc["v"]), 70 * 90)
        # 한반도 커버리지 상식 체크(DFS 도메인 대략 121~132E, 32~44N)
        self.assertTrue(119 < doc["west"] < 124)
        self.assertTrue(129 < doc["east"] < 134)
        self.assertTrue(30 < doc["south"] < 34)
        self.assertTrue(41 < doc["north"] < 45)
        valid_u = [x for x in doc["u"] if x is not None]
        self.assertTrue(all(abs(x - 3.0) < 0.3 for x in valid_u))
        self.assertIn(None, doc["u"])  # 결측 전파


if __name__ == "__main__":
    unittest.main()
