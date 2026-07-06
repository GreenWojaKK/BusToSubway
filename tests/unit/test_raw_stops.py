# raw_stops 로더 — 실데이터 raw 불변식 (audit/routes_stops.md §5, §6)
from bts.io import raw_stops


def test_before_로더_불변식():
    df = raw_stops.load_before()
    assert len(df) == 3409                      # stop 우주 [RS§5]
    assert df["stop_id"].is_unique
    assert list(df.columns) == ["stop_id", "stop_name", "stop_lat", "stop_lon"]
    assert df["stop_id"].str.startswith("BS_").all()
    # stop_name 유니크 1,759 == 선행 place 기준값과 동수 (신호 — [RS§5])
    assert df["stop_name"].nunique() == 1759
    # 공인 조인기 양성 대조군의 전제: stop 우주에는 '양우내안애' 표기만 존재
    names = set(df["stop_name"])
    assert "양우내안애" in names and "양우내안에" not in names


def test_after_로더_float_함정_정규화():
    df = raw_stops.load_after()
    assert len(df) == 3224                      # [RS§1]
    assert df["stop_id"].is_unique
    assert not df["stop_id"].str.contains(r"\.", regex=True).any()   # '.0' strip 완료
    assert df["stop_id"].str.fullmatch(r"\d+").all()
