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


if __name__ == "__main__":
    unittest.main()
