import unittest

from pipeline import frames

B = {"west": 1.0, "south": 2.0, "east": 3.0, "north": 4.0}


class FramesTest(unittest.TestCase):
    def test_tm_to_iso(self):
        self.assertEqual(frames.tm_to_iso("202607112335"),
                         "2026-07-11T23:35:00+09:00")

    def test_build_sorted_by_time(self):
        past = [("202607112335", "frames/past/202607112335.png", B),
                ("202607112330", "frames/past/202607112330.png", B)]
        nc = [("202607112350", "frames/nowcast/202607112350.png", B)]
        out = frames.build_frames_json(past, nc, "2026-07-11T23:40:00+09:00")
        times = [f["time"] for f in out["frames"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(out["frames"][0]["kind"], "past")
        self.assertEqual(out["frames"][-1]["kind"], "nowcast")
        self.assertEqual(out["generated"], "2026-07-11T23:40:00+09:00")

    def test_wind_entries_sorted_and_optional(self):
        out = frames.build_frames_json([], [], "g",
                                       wind_entries=[("202607121500", "wind/202607121500.json"),
                                                     ("202607121400", "wind/202607121400.json")])
        self.assertEqual([w["time"] for w in out["wind"]],
                         ["2026-07-12T14:00:00+09:00", "2026-07-12T15:00:00+09:00"])
        self.assertEqual(frames.build_frames_json([], [], "g")["wind"], [])

    def test_reusable_paths(self):
        old = {"frames": [{"path": "frames/past/a.png"},
                          {"path": "frames/past/b.png"}]}
        got = frames.reusable_paths(old, ["frames/past/b.png",
                                          "frames/past/c.png"])
        self.assertEqual(got, {"frames/past/b.png"})
        self.assertEqual(frames.reusable_paths(None, ["x"]), set())
        self.assertEqual(frames.reusable_paths({"bogus": 1}, ["x"]), set())

    def test_nowcast_grid_attached_optional(self):
        B = {"west": 1.0, "south": 2.0, "east": 3.0, "north": 4.0}
        nc = [("202607121510", "frames/nowcast/202607121510.png", B),
              ("202607121520", "frames/nowcast/202607121520.png", B)]
        out = frames.build_frames_json(
            [], nc, "g",
            nowcast_grids={"202607121510": "precip/202607121510.json"})
        by_path = {f["path"]: f for f in out["frames"]}
        self.assertEqual(by_path["frames/nowcast/202607121510.png"]["grid"],
                         "precip/202607121510.json")
        self.assertNotIn("grid",
                         by_path["frames/nowcast/202607121520.png"])
        # 기본값(None)이면 grid 키 없음
        out2 = frames.build_frames_json([], nc, "g")
        self.assertTrue(all("grid" not in f for f in out2["frames"]))

    def test_wind_refresh_needed(self):
        target = "2026-07-12T21:00:00+09:00"
        self.assertTrue(frames.wind_refresh_needed(None, target))
        self.assertTrue(frames.wind_refresh_needed({"bogus": 1}, target))
        self.assertTrue(frames.wind_refresh_needed(
            {"wind": [{"time": "2026-07-12T20:00:00+09:00"}]}, target))
        self.assertFalse(frames.wind_refresh_needed(
            {"wind": [{"time": "2026-07-12T21:00:00+09:00"}]}, target))
        self.assertTrue(frames.wind_refresh_needed({"wind": []}, target))


if __name__ == "__main__":
    unittest.main()
