# raw_stops 로더 테스트 — 실제 stop 원천 파일의 구조와 기본 불변식을 확인한다.
from dataio import raw_stops


def test_before_로더_불변식():
    df = raw_stops.load_before()
    assert len(df) == 3409                      # stop 우주 [RS§5]
    assert df["stop_id"].is_unique
    assert list(df.columns) == ["stop_id", "stop_name", "stop_lat", "stop_lon"]
    assert df["stop_id"].str.startswith("BS_").all()
    # stop_name 고유값 수는 place 구성 전에 확인하는 입력 신호다.
    assert df["stop_name"].nunique() == 1759
    # alias 테스트의 전제: 원천 stop 목록에는 보정 후 표기만 존재한다.
    names = set(df["stop_name"])
    assert "양우내안애" in names and "양우내안에" not in names


def test_after_로더_float_함정_정규화():
    df = raw_stops.load_after()
    assert len(df) == 3224                      # [RS§1]
    assert df["stop_id"].is_unique
    assert not df["stop_id"].str.contains(r"\.", regex=True).any()   # '.0' 접미사를 제거했다.
    assert df["stop_id"].str.fullmatch(r"\d+").all()
