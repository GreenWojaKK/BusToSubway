# s00 before 체크 테스트 — 합성 데이터로 실패해야 할 조건을 직접 주입한다.
# 각 체크 함수가 놓치면 안 되는 입력을 실제로 FAIL 처리하는지 확인한다.
import pandas as pd
import pytest

from checks import diff
from checks.contracts import s00_before as chk


def _st(rows):
    """합성 stop_times 프레임: (trip_id, pattern_id, stop_id, seq, arr_s, dep_s, lineage)."""
    df = pd.DataFrame(rows, columns=["trip_id", "pattern_id", "stop_id",
                                     "seq", "arr_s", "dep_s", "lineage"])
    df["route_name"] = df["pattern_id"]
    return df


# ── C-S00-B-003: (trip_id, seq) 유일 ─────────────────────────────────────────
def test_c003_dup_key_fails():
    st = _st([("T1", "P1", "S1", 1, 100, 100, "TAGO"),
              ("T1", "P1", "S2", 1, 200, 200, "TAGO")])   # seq 중복 주입
    assert chk.c003_row_key(st).status == "FAIL"


def test_c003_unique_passes():
    st = _st([("T1", "P1", "S1", 1, 100, 100, "TAGO"),
              ("T1", "P1", "S2", 2, 200, 200, "TAGO")])
    assert chk.c003_row_key(st).status == "PASS"


# ── C-S00-B-008 [PC]: dwell>600s 정확히 31행 전부 ACC0 ──────────────────────
def _dwell_frame(n_viol, lineage="ACC0"):
    rows = [(f"T{i}", "P", "S", 1, 1000 * i + 20000, 1000 * i + 20700, lineage)
            for i in range(n_viol)]                        # dwell 700s > 600s
    rows += [(f"N{i}", "P", "S", 1, 30000, 30010, "TAGO") for i in range(3)]
    return _st(rows)


def test_c008_exact_31_passes():
    assert chk.c008_dwell_pc(_dwell_frame(31)).status == "PASS"


def test_c008_30_fails():
    # dwell 이상치가 30행이면 기대한 31행 조건을 만족하지 못한다.
    assert chk.c008_dwell_pc(_dwell_frame(30)).status == "FAIL"


def test_c008_zero_fails():
    # 이상치가 사라져도 통과하면 안 된다. positive_control이 이 회귀를 잡는다.
    r = chk.c008_dwell_pc(_dwell_frame(0))
    assert r.status == "FAIL" and r.positive_control


def test_c008_wrong_lineage_fails():
    assert chk.c008_dwell_pc(_dwell_frame(31, lineage="TAGO")).status == "FAIL"


# ── C-S00-B-009 [PC]: seq 연속 위반 정확히 18 trips (offset 8 + 결번 10) ─────
def _seq_frame(n_offset=8, n_gap=10, gap_lineage="ACC0"):
    rows = []
    for i in range(n_offset):     # offset 시작: min!=1, 내부 연속
        for s in (2, 3, 4):
            rows.append((f"O{i}", "P", "S", s, 100 + s, 100 + s, "ACC0"))
    for i in range(n_gap):        # 내부 결번: max != count
        for s in (1, 2, 4):
            rows.append((f"G{i}", "P", "S", s, 100 + s, 100 + s, gap_lineage))
    for i in range(5):            # 정상 trip
        for s in (1, 2, 3):
            rows.append((f"N{i}", "P", "S", s, 100 + s, 100 + s, "TAGO"))
    return _st(rows)


def test_c009_exact_18_passes():
    assert chk.c009_seq_pc(_seq_frame()).status == "PASS"


def test_c009_17_fails():
    assert chk.c009_seq_pc(_seq_frame(n_gap=9)).status == "FAIL"


def test_c009_non_acc0_fails():
    assert chk.c009_seq_pc(_seq_frame(gap_lineage="TAGO")).status == "FAIL"


# ── C-S00-B-002: 메타 기반 행수 계약 ─────────────────────────────────────────
def _meta_ok():
    return {"schedule": {"raw_rows": 427527, "allnull_rows": 48,
                         "allnull_contiguous": True, "partial_null_rows": 0,
                         "dup_full_rows": 0}}


def test_c002_meta_pass_and_fail():
    st = pd.DataFrame(index=range(427479), columns=["trip_id"], data="t")
    assert chk.c002_schedule_rows(_meta_ok(), st).status == "PASS"
    bad = _meta_ok()
    bad["schedule"]["allnull_rows"] = 47      # 스펙 §7.2 '전결측 47행' 검출
    assert chk.c002_schedule_rows(bad, st).status == "FAIL"


# ── C-S00-B-013 [PC]: name→stop 해소 메타 ───────────────────────────────────
def test_c013_pass_and_fail():
    ok = {"join": {"n_alias": 1, "n_fail": 0,
                   "resolved_max_dist_m": 0.91, "resolved_stop_nunique": 3409}}
    assert chk.c013_joiner_pc(ok, 1.0).status == "PASS"
    zero_alias = {"join": {"n_alias": 0, "n_fail": 0,
                           "resolved_max_dist_m": 0.91, "resolved_stop_nunique": 3409}}
    assert chk.c013_joiner_pc(zero_alias, 1.0).status == "FAIL"   # alias를 못 찾으면 실패
    too_far = {"join": {"n_alias": 1, "n_fail": 0,
                        "resolved_max_dist_m": 1.5, "resolved_stop_nunique": 3409}}
    assert chk.c013_joiner_pc(too_far, 1.0).status == "FAIL"


# ── C-S00-B-016: role 분포 exact ────────────────────────────────────────────
def test_c016_role_dist_change_fails():
    # 최소 합성 vt로 role 분포 drift가 독립적으로 실패하는지 확인한다.
    vt = pd.DataFrame({
        "pattern_id": [f"P{i}" for i in range(3)],
        "role": ["main", "main", "circular"],
        "frequency": [1, 1, 1],
        "base_pattern_id_raw": [None, None, None],
        "source": ["auto", "auto", "auto"],
        "verified": [False, False, False],
        "n_stops": [2, 2, 2],
    })
    tr = pd.DataFrame({"trip_id": ["T1", "T2", "T3"],
                       "pattern_id": ["P0", "P1", "P2"]})
    ev_nstops = {f"P{i}": 2 for i in range(3)}
    assert chk.c016_variant_tags(vt, tr, ev_nstops).status == "FAIL"


# ── D-S00-B-001: 노선 분류 diff — 합성 base 우주로 MATCH/UNEXPLAINED ─────────
def _class_universe():
    names = [str(i) for i in range(1, 161)]                    # 숫자 1~3자리 160
    names += [f"울주{i:02d}" for i in range(1, 11)]             # 울주 10
    names += [str(n) for n in range(5001, 5015)]               # 4자리 14 (급행)
    names += ["13 지원2", "236 지원2", "236 지원3", "236 지원4",
              "802 지원3", "924 지원2"]                          # 지원 6
    return names


def test_d001_match_and_unexplained(monkeypatch, tmp_path):
    st = pd.DataFrame({"route_name": _class_universe()})
    ru = pd.DataFrame({"route_name": _class_universe() + ["김해공항", "50(내고산 방면)"]})
    r = chk.d001_route_classes(st, ru)
    assert r.status == "MATCH"

    # 급행 1개를 제거하면 설명되지 않은 차이로 분류된다.
    monkeypatch.setattr(diff, "make_stub",
                        lambda *a, **kw: tmp_path / "stub.md")
    st2 = pd.DataFrame({"route_name": [n for n in _class_universe() if n != "5001"]})
    ru2 = pd.DataFrame({"route_name": [n for n in ru["route_name"] if n != "5001"]})
    r2 = chk.d001_route_classes(st2, ru2)
    assert r2.status == "UNEXPLAINED"


# ── P-S00-B-001†: 좌표 bbox — 정확히 2건(김해공항)만 허용 ────────────────────
def _stops(extra_out=0):
    rows = [("P1", "국제선청사", 35.179, 128.938), ("P2", "국내선", 35.179, 128.945)]
    rows += [(f"U{i}", f"울산{i}", 35.5, 129.3) for i in range(5)]
    rows += [(f"X{i}", f"밖{i}", 36.5, 127.0) for i in range(extra_out)]
    df = pd.DataFrame(rows, columns=["stop_id", "stop_name", "lat", "lon"])
    df["lineage"] = "TAGO"
    df["in_schedule"] = True
    return df


def test_p001_pass_and_fail():
    assert chk.p001_coords(_stops()).status == "PASS"
    r = chk.p001_coords(_stops(extra_out=1))
    assert r.status == "FAIL"
    assert r.check_class == "CONTRACT"     # 감사 실측 범위라 CONTRACT로 유지한다.
