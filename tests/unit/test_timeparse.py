# timeparse 단위 테스트 (verification.md §6.2, stage1 spec §7.1)
from datetime import date, datetime

import pytest

from bts.io import timeparse as tp


class TestHms24:
    def test_zero_pad_없음(self):
        assert tp.hms24_to_sec("7:10:00") == 25800

    def test_24시_연장_표기(self):
        assert tp.hms24_to_sec("25:10:01") == 90601

    def test_2자리_시(self):
        assert tp.hms24_to_sec("14:00:30") == 50430

    @pytest.mark.parametrize("bad", ["07:60:00", "7:10", "abc", "3:00:00", "26:00:00", ""])
    def test_이형_및_창_밖_ValueError(self, bad):
        with pytest.raises(ValueError):
            tp.hms24_to_sec(bad)


class TestKrAmpm:
    def test_오전12_은_0시(self):
        ts = tp.parse_kr_ampm("2025-05-06 오전 12:05:00")
        assert ts == datetime(2025, 5, 6, 0, 5, 0)

    def test_오후12_는_12시(self):
        ts = tp.parse_kr_ampm("2025-05-06 오후 12:05:00")
        assert ts == datetime(2025, 5, 6, 12, 5, 0)

    def test_오후_일반(self):
        ts = tp.parse_kr_ampm("2025-05-06 오후 3:20:11")
        assert ts == datetime(2025, 5, 6, 15, 20, 11)

    @pytest.mark.parametrize("s", ["0001-01-01", "2025-05-07 0:00"])
    def test_sentinel_2종은_None(self, s):
        assert tp.parse_kr_ampm(s) is None

    @pytest.mark.parametrize("bad", ["2025-05-06 12:00:00", "2025-05-06 새벽 1:00:00", "gibberish"])
    def test_이형은_ValueError(self, bad):
        # 계약 C-S00-A-004: sentinel도 regex도 아니면 '그 외 이형 0'
        with pytest.raises(ValueError):
            tp.parse_kr_ampm(bad)


class TestServiceS:
    def test_당일(self):
        # 오전 12:05 == 0시 5분 → 창 밖(4시 이전) — 대신 5시 확인
        assert tp.to_service_s(datetime(2025, 5, 6, 5, 0, 0), date(2025, 5, 6)) == 18000

    def test_익일_새벽은_24h_wrap(self):
        # 05-07 01:10 → 25h10m — [4h,26h) 안
        assert tp.to_service_s(datetime(2025, 5, 7, 1, 10, 0), date(2025, 5, 6)) == 90600

    def test_창_초과_거부(self):
        # 05-07 02:00 → 26h == 93600 — 창 밖 거부 (verification.md §6.2)
        with pytest.raises(ValueError):
            tp.to_service_s(datetime(2025, 5, 7, 2, 0, 0), date(2025, 5, 6))

    def test_창_이전_거부(self):
        with pytest.raises(ValueError):
            tp.to_service_s(datetime(2025, 5, 6, 3, 59, 59), date(2025, 5, 6))
