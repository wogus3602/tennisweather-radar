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

    def test_past_grid_attached_to_latest_only(self):
        """관측 격자는 '최신 과거 프레임 1장'에만 붙는 순수 추가(additive).

        와이어 계약: 출시된 앱·Cloud Function이 frames.json을 파싱한다. 기존
        필드는 한 톨도 바뀌면 안 되고, 나머지 과거 프레임엔 grid가 없어야 한다
        (구버전은 kind=="nowcast"만 걷으므로 이 키를 무시하고 지나간다).
        """
        past = [("202607142125", "frames/past/202607142125.png", B),
                ("202607142130", "frames/past/202607142130.png", B)]
        nc = [("202607142140", "frames/nowcast/202607142140.png", B)]
        out = frames.build_frames_json(
            past, nc, "g",
            nowcast_grids={"202607142140": "precip/202607142140.json"},
            past_grids={"202607142130": "precip/obs_202607142130.json"})
        by_path = {f["path"]: f for f in out["frames"]}

        latest = by_path["frames/past/202607142130.png"]
        self.assertEqual(latest["grid"], "precip/obs_202607142130.json")
        self.assertEqual(latest["kind"], "past")
        self.assertEqual(latest["bounds"], B)
        self.assertEqual(latest["time"], "2026-07-14T21:30:00+09:00")
        self.assertNotIn("grid", by_path["frames/past/202607142125.png"])
        # nowcast 항목은 그대로(키 집합까지 동일)
        nowcast = by_path["frames/nowcast/202607142140.png"]
        self.assertEqual(set(nowcast), {"time", "path", "kind", "bounds",
                                        "grid"})
        self.assertEqual(nowcast["grid"], "precip/202607142140.json")
        # 기본값이면 과거 프레임에 grid 키 자체가 없음(하위 호환)
        out2 = frames.build_frames_json(past, nc, "g")
        self.assertTrue(all("grid" not in f for f in out2["frames"]))

    def test_past_and_nowcast_grids_do_not_cross_contaminate(self):
        """past tm과 nowcast valid_tm은 겹칠 수 있다(HSP 21:30 = QPF base 21:20+10).

        그래서 격자 맵을 하나로 합치면 안 되고, 파일명도 obs_ 접두로 분리한다.
        """
        tm = "202607142130"
        out = frames.build_frames_json(
            [(tm, f"frames/past/{tm}.png", B)],
            [(tm, f"frames/nowcast/{tm}.png", B)], "g",
            nowcast_grids={tm: f"precip/{tm}.json"},
            past_grids={tm: f"precip/obs_{tm}.json"})
        by_path = {f["path"]: f for f in out["frames"]}
        self.assertEqual(by_path[f"frames/past/{tm}.png"]["grid"],
                         f"precip/obs_{tm}.json")
        self.assertEqual(by_path[f"frames/nowcast/{tm}.png"]["grid"],
                         f"precip/{tm}.json")

    def test_wind_refresh_needed(self):
        target = "2026-07-12T21:00:00+09:00"
        self.assertTrue(frames.wind_refresh_needed(None, target))
        self.assertTrue(frames.wind_refresh_needed({"bogus": 1}, target))
        self.assertTrue(frames.wind_refresh_needed(
            {"wind": [{"time": "2026-07-12T20:00:00+09:00"}]}, target))
        self.assertFalse(frames.wind_refresh_needed(
            {"wind": [{"time": "2026-07-12T21:00:00+09:00"}]}, target))
        self.assertTrue(frames.wind_refresh_needed({"wind": []}, target))


class PublishableNowcastTest(unittest.TestCase):
    """빈 예측 프레임이 '데이터 구멍'인지 '맑은 날'인지 가르는 판정.

    0을 무조건 버리면 맑은 날 나우캐스트가 통째로 사라지고, 0을 무조건 실으면
    비 오는 날 비구름이 한 프레임 사라졌다 되돌아온다. 그래서 절대값이 아니라
    '지금 비가 오는가'로 판정한다.
    """

    def test_blank_between_echoes_is_a_data_hole(self):
        # 운영에서 실제로 터진 모양: 23:30 에코 → 23:40 완전 투명 → 23:50 에코.
        self.assertEqual(
            frames.publishable_nowcast([5, 0, 7], observed_echo=9),
            [True, False, True])

    def test_clear_sky_keeps_every_frame(self):
        # 관측도 예측도 0 = 진짜로 비가 안 온다. 이걸 버리면 맑은 날 예측이 사라진다.
        self.assertEqual(
            frames.publishable_nowcast([0, 0, 0], observed_echo=0),
            [True, True, True])

    def test_all_blank_while_it_is_raining_is_dropped(self):
        # QPF 전면 장애: 지금 비가 오는데 예측이 전부 비었다 → 예측 전체가 거짓.
        self.assertEqual(
            frames.publishable_nowcast([0, 0, 0], observed_echo=9),
            [False, False, False])

    def test_leading_and_trailing_blanks_dropped_when_raining(self):
        self.assertEqual(
            frames.publishable_nowcast([0, 3, 0], observed_echo=0),
            [False, True, False])

    def test_unknown_observation_falls_back_to_forecast_only(self):
        # 관측 측정 실패(None) — 예측 안에 에코가 있으면 빈 프레임은 구멍이고,
        # 예측이 전부 비었으면 맑은 날로 본다(관측이 없으니 반증할 근거가 없다).
        self.assertEqual(
            frames.publishable_nowcast([0, 3], observed_echo=None),
            [False, True])
        self.assertEqual(
            frames.publishable_nowcast([0, 0], observed_echo=None),
            [True, True])

    def test_empty_input(self):
        self.assertEqual(frames.publishable_nowcast([], observed_echo=9), [])


if __name__ == "__main__":
    unittest.main()
