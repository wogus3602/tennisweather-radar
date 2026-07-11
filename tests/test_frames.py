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

    def test_reusable_paths(self):
        old = {"frames": [{"path": "frames/past/a.png"},
                          {"path": "frames/past/b.png"}]}
        got = frames.reusable_paths(old, ["frames/past/b.png",
                                          "frames/past/c.png"])
        self.assertEqual(got, {"frames/past/b.png"})
        self.assertEqual(frames.reusable_paths(None, ["x"]), set())
        self.assertEqual(frames.reusable_paths({"bogus": 1}, ["x"]), set())


if __name__ == "__main__":
    unittest.main()
