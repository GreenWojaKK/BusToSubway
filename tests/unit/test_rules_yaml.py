# route_class_rules yaml 자가 정합성 (stage1 spec §7.1의 데이터 측 절반)
# get_rules(era) API 자체는 s01 구현자 소유 — 여기서는 규칙 파일의 계약 형식을 검증한다.
import yaml

import bts.paths as paths


def _load(era):
    with open(paths.CONFIG / f"route_class_rules.{era}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestBeforeRules:
    def test_expect_bases_자가_검증(self):
        r = _load("before")
        by_class = {d["class"]: d["expect_bases"] for d in r["rules"]}
        # general(160)+ulju(10)=170, +express 14 = 정규 184, +support 6 +limousine 1 = catalog 191
        assert by_class["general"] + by_class["ulju"] == 170
        assert by_class["general"] + by_class["ulju"] + by_class["express"] == r["accounting"]["regular_bases"] == 184
        assert sum(by_class.values()) == r["accounting"]["catalog_bases"] == 191

    def test_portability_forbidden(self):
        assert _load("before")["portability"] == "forbidden"

    def test_era_키_명시(self):
        assert _load("before")["era"] == "before"


class TestAfterRules:
    def test_경성_항등은_합계_184뿐(self):
        r = _load("after")
        assert r["accounting"]["total_names"] == 184

    def test_yangsan_규칙이_최우선(self):
        r = _load("after")
        assert r["rules"][0]["class"] == "yangsan"
        assert r["rules"][0]["rule"] == "pattern_prefix:388"

    def test_portability_forbidden(self):
        assert _load("after")["portability"] == "forbidden"

    def test_before_이식_반증_가능(self):
        # before express 규칙(fullmatch:\d{4})을 after에 이식하면 26개(실측)로 expect 14가 깨진다.
        # 여기서는 두 파일의 era가 서로 달라 scope 미지정 조회가 불가능함을 형식으로 확인한다.
        assert _load("before")["era"] != _load("after")["era"]


def test_era_scope_미지정_조회_불가():
    # era scope 미지정 규칙 조회 금지 (design.md §2.5) — 파일 명명 자체가 era 키를 요구한다
    import pytest
    with pytest.raises(FileNotFoundError):
        _load("전체")
