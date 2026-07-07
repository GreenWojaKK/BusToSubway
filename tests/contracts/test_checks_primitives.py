# 위반 주입 fixture — 체크 primitive가 실패 조건을 실제로 잡는지 확인한다.
# 각 primitive에 깨진 합성 데이터를 넣어 PASS로 새는 회귀를 막는다.
import pandas as pd

from bts.checks import core


class TestRowCount:
    def test_전결측_48행_계약의_위반_검출(self):
        # 시나리오: 전결측 47행짜리 schedule 표본 → C-S00-B-002류 FAIL (48 아님)
        blank_rows = pd.DataFrame({"a": [None] * 47})
        r = core.row_count("C-S00-B-002", "CONTRACT", blank_rows, 48, "audit/schedule_before§1")
        assert r.status == "FAIL"
        assert r.observed == 47 and r.expected == 48

    def test_정상은_PASS(self):
        r = core.row_count("C-X", "CONTRACT", pd.DataFrame({"a": [1, 2]}), 2, "t")
        assert r.status == "PASS"


class TestUniqueKey:
    def test_trip_seq_중복_1행_주입_검출(self):
        # 시나리오: (trip_id, seq) 중복 1행 → C-S00-B-003 FAIL
        df = pd.DataFrame({"trip_id": ["T1", "T1", "T2"], "seq": [1, 1, 1]})
        r = core.unique_key("C-S00-B-003", "CONTRACT", df, ["trip_id", "seq"], "audit/schedule_before§2")
        assert r.status == "FAIL"
        assert r.observed["dup_rows"] == 1


class TestPositiveControl:
    def test_dwell_이상치_소실_검출(self):
        # 시나리오: dwell>600s 표본이 30행(31 아님) → 양성 대조군의 자기 검증 (C-S00-B-008)
        dwell = pd.Series([700] * 30)
        n_over = int((dwell > 600).sum())
        r = core.check_eq("C-S00-B-008", "CONTRACT", n_over, 31,
                          "audit/schedule_before§4", positive_control=True)
        assert r.status == "FAIL"           # 이상치가 사라져도 FAIL — 검증기가 눈을 뜨고 있다
        assert r.positive_control

    def test_sentinel_행수_감소_검출(self):
        # 시나리오: dep sentinel 8,301행 표본 → C-S00-A-004 FAIL
        r = core.check_eq("C-S00-A-004", "CONTRACT", 8301, 8302,
                          "audit/schedule_after§4", positive_control=True)
        assert r.status == "FAIL"


class TestEnum:
    def test_role_미지값_주입_검출(self):
        # 시나리오: role에 미지값 'loop' 1행 → enum 체크 FAIL (role 8종 + support)
        roles = pd.Series(["main", "circular", "short_turn", "loop"])
        allowed = ["main", "circular", "short_turn", "branch", "detour",
                   "extension", "duplicate", "anomaly", "support"]
        r = core.in_enum("C-VT-ENUM", "CONTRACT", roles, allowed, "audit/variant_tags§1")
        assert r.status == "FAIL"
        assert r.observed["unknown_values"] == ["loop"]


class TestAccounting:
    def test_처리결과표_detour_1행_누락_검출(self):
        # 시나리오: 패턴 분류표에서 detour 1행 누락 → C-S01-B-005 accounting FAIL (486≠487)
        parts = {"canonical": 379, "excl_circular_with_base": 37, "excl_detour": 50,
                 "excl_extension": 10, "excl_duplicate": 2, "excl_anomaly": 2, "support": 6}
        r = core.accounting("C-S01-B-005", parts, 487, "audit/variant_tags§2,§3")
        assert r.status == "FAIL"
        assert r.observed["sum"] == 486     # 어느 항이 새는지 즉시 판독 가능
        assert r.observed["parts"]["excl_detour"] == 50

    def test_전수_회계_성립은_PASS(self):
        parts = {"canonical": 379, "excluded": 102, "support": 6}
        assert core.accounting("C-S01-B-005", parts, 487, "t").status == "PASS"

    def test_canonical_413_변경_검출(self):
        # 시나리오: 전 role self→NaN 정규화 흉내(circular 자기참조 34 소거) → canonical 413≠379
        # (C-S01-B-004의 골자 — main 187 + circular base 없는 66+34 + short_turn 112 + branch 14)
        parts = {"main": 187, "circular_baseless": 100, "short_turn": 112, "branch": 14}
        r = core.accounting("C-S01-B-004", parts, 379, "audit/variant_tags§6")
        assert r.status == "FAIL"
        assert r.observed["sum"] == 413     # 413≠379 검출


class TestMonotonic:
    def test_seq_역행_미절단_검출(self):
        # 시나리오: trip 경계 미절단으로 그룹 내 seq 역행 잔존 → C-S01-A-002 FAIL
        df = pd.DataFrame({"trip": ["t1"] * 4, "seq": [1, 2, 3, 2]})
        r = core.monotonic("C-S01-A-002", "CONTRACT", df, ["trip"], "seq", "audit/schedule_after§5.1")
        assert r.status == "FAIL"

    def test_strict_단조는_PASS(self):
        df = pd.DataFrame({"trip": ["t1"] * 3, "seq": [1, 2, 3]})
        assert core.monotonic("C-X", "CONTRACT", df, ["trip"], "seq", "t").status == "PASS"


class TestRegexRangeFk:
    def test_regex_이형_검출(self):
        s = pd.Series(["BR_TAGO_USB123456789012", "BR_BAD_1"])
        r = core.regex_all("C-S00-B-006", "CONTRACT", s,
                           r"BR_(TAGO_USB\d{12}|ACC0_\d{8})", "audit/schedule_before§10")
        assert r.status == "FAIL"

    def test_value_range_창_밖_검출(self):
        s = pd.Series([14400, 93600])
        r = core.value_range("C-S00-B-007", "CONTRACT", s, 14400, 93600,
                             "audit/schedule_before§4", right_open=True)
        assert r.status == "FAIL"           # 93600 == 26h는 [4h,26h) 밖

    def test_fk_dangling_검출(self):
        child = pd.Series(["P1", "P9"])
        parent = pd.Series(["P1", "P2"])
        r = core.fk_subset("C-S00-B-011", "CONTRACT", child, parent, "audit/schedule_before§8")
        assert r.status == "FAIL"
        assert r.observed["dangling_count"] == 1

    def test_functional_위반_검출(self):
        df = pd.DataFrame({"pattern": ["p1", "p1"], "route": ["A", "B"]})
        r = core.functional("C-S00-B-005", "CONTRACT", df, "pattern", "route",
                            "audit/schedule_before§2")
        assert r.status == "FAIL"


class TestDumpSample:
    def test_실패_표본_덤프(self, sandbox, tmp_path):
        import bts.paths as paths
        vdir = paths.ARTIFACTS / "t90_dummy" / "before" / "v001"
        vdir.mkdir(parents=True)
        with paths.build_context(vdir):
            p = core.dump_sample(vdir, "C-TEST-001",
                                 pd.DataFrame({"bad": range(500)}), limit=200)
        out = pd.read_csv(p)
        assert len(out) == 200              # 상한 200행
        assert p.endswith("C-TEST-001_sample.csv")
