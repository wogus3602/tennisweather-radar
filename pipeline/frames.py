"""frames.json 조립과 기존 산출물 재사용 판단 — I/O 없는 순수 함수."""


def tm_to_iso(tm: str) -> str:
    return (f"{tm[0:4]}-{tm[4:6]}-{tm[6:8]}"
            f"T{tm[8:10]}:{tm[10:12]}:00+09:00")


def build_frames_json(past, nowcast, generated_iso, wind_entries=()):
    """past/nowcast: (tm, path, bounds) 목록 → 스키마 dict(time 오름차순).

    wind_entries: (tm, path) 목록 → doc["wind"] = [{"time","path"}](오름차순).
    """
    out = []
    for kind, items in (("past", past), ("nowcast", nowcast)):
        for tm, path, bounds in items:
            out.append({"time": tm_to_iso(tm), "path": path,
                        "kind": kind, "bounds": bounds})
    out.sort(key=lambda f: f["time"])
    wind = sorted(
        [{"time": tm_to_iso(tm), "path": p} for tm, p in wind_entries],
        key=lambda w: w["time"])
    return {"generated": generated_iso, "frames": out, "wind": wind}


def reusable_paths(old_frames_json, wanted_paths):
    """이전 배포 frames.json에 있던 path 중 이번에도 원하는 것들."""
    if not isinstance(old_frames_json, dict):
        return set()
    old = {f.get("path") for f in old_frames_json.get("frames", [])
           if isinstance(f, dict)}
    return old & set(wanted_paths)
