# s00 before 로더 3종 — raw 불변식 위반 주입 테스트 (검증기의 검증, verification.md §6)
# 실데이터 로드는 module 스코프 fixture 1회 — 위반 주입은 in-memory 사본으로 수행한다.
import pandas as pd
import pytest

import bts.paths as paths
from bts.io import ContractViolation
from bts.io import raw_bus_route_before, raw_schedule_before, raw_variant_tags


# ── kr_hms_to_sec — bus_route_before 고유 'H시 M분 S초' 파서 ─────────────────
def test_kr_hms_basic():
    assert raw_bus_route_before.kr_hms_to_sec("7시 10분 0초") == 25800
    assert raw_bus_route_before.kr_hms_to_sec("4시 0분 0초") == 14400


def test_kr_hms_24plus():
    # 24+시 연장 표기 (자정 넘김 — [RS§7])
    assert raw_bus_route_before.kr_hms_to_sec("25시 10분 1초") == 90601


def test_kr_hms_rejects():
    with pytest.raises(ValueError):
        raw_bus_route_before.kr_hms_to_sec("7:10:00")        # 콜론 포맷은 이 파서 소관 아님
    with pytest.raises(ValueError):
        raw_bus_route_before.kr_hms_to_sec("3시 0분 0초")     # service_s 창 [4h,26h) 밖
    with pytest.raises(ValueError):
        raw_bus_route_before.kr_hms_to_sec("7시 61분 0초")    # 분 범위 위반


# ── variant_tags 로더 ────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def vt_raw():
    return pd.read_csv(paths.raw_path("variant_tagging/variant_tags.csv"),
                       encoding="utf-8-sig", dtype=str)


def test_vt_load_ok_and_raw_preserved(vt_raw):
    df = raw_variant_tags.validate(vt_raw.copy())
    assert df.shape == (481, 18)
    # ★ base_route_id 원형 보존 — main 자기참조 92건이 결측으로 정규화되지 않았다 [VT§3]
    main_self = ((df["role"] == "main")
                 & (df["base_route_id"] == df["route_id"])).sum()
    assert main_self == 92
    circ_self = ((df["role"] == "circular")
                 & (df["base_route_id"] == df["route_id"])).sum()
    assert circ_self == 34


def test_vt_unknown_role_rejected(vt_raw):
    # 위반 주입: role 미지값 'loop' 1행 → 로딩 즉시 실패 (role 8종 enum)
    bad = vt_raw.copy()
    bad.loc[bad.index[0], "role"] = "loop"
    with pytest.raises(ContractViolation):
        raw_variant_tags.validate(bad)


def test_vt_dup_pk_rejected(vt_raw):
    bad = vt_raw.copy()
    bad.loc[bad.index[1], "route_id"] = bad.loc[bad.index[0], "route_id"]
    with pytest.raises(ContractViolation):
        raw_variant_tags.validate(bad)


def test_vt_dangling_base_rejected(vt_raw):
    bad = vt_raw.copy()
    notna = bad["base_route_id"].notna()
    bad.loc[bad.index[notna][0], "base_route_id"] = "BR_TAGO_USB999999999999"
    with pytest.raises(ContractViolation):
        raw_variant_tags.validate(bad)


def test_vt_source_verified_equiv_rejected(vt_raw):
    bad = vt_raw.copy()
    agent_idx = bad.index[bad["source"] == "agent"][0]
    bad.loc[agent_idx, "verified"] = "False"
    with pytest.raises(ContractViolation):
        raw_variant_tags.validate(bad)


def test_vt_evidence_1to1():
    ev = raw_variant_tags.load_evidence()
    assert len(ev) == 184
    assert sum(doc["n_variants"] for doc in ev.values()) == 481


# ── bus_route_before 로더 ────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def bus_raw():
    return pd.read_parquet(paths.raw_path("ulsan_bus_route_before.parquet"))


def test_bus_load_ok(bus_raw):
    meta = {}
    df = raw_bus_route_before.validate(bus_raw.copy(), meta)
    assert len(df) == 21402
    assert meta["route_names"] == 400
    assert meta["unique_name_coord"] == 3409


def test_bus_dup_key_rejected(bus_raw):
    bad = bus_raw.copy()
    bad.loc[bad.index[1], ["노선명", "정류장순서"]] = bad.loc[
        bad.index[0], ["노선명", "정류장순서"]].values
    with pytest.raises(ContractViolation):
        raw_bus_route_before.validate(bad)


def test_bus_token_count_rejected(bus_raw):
    bad = bus_raw.copy()
    bad.loc[bad.index[0], "도착횟수"] = str(int(bad.loc[bad.index[0], "도착횟수"]) + 1)
    with pytest.raises(ContractViolation):
        raw_bus_route_before.validate(bad)


# ── schedule_before 로더 (module 1회 로드, 주입은 in-memory) ──────────────────
@pytest.fixture(scope="module")
def sch_raw():
    return pd.read_parquet(paths.raw_path("ulsan_route_schedule_before.parquet"))


def test_schedule_load_ok(sch_raw):
    meta = {}
    df = raw_schedule_before.validate(sch_raw.copy(), meta)
    assert len(df) == 427479
    assert meta["allnull_rows"] == 48 and meta["allnull_contiguous"] is True
    assert meta["trip_ids"] == 7625 and meta["pattern_ids"] == 487


def test_schedule_allnull_count_rejected(sch_raw):
    # 위반 주입: 전결측 48행 중 1행에 값 주입 → 전결측 47 (스펙 §7.2 '전결측 47행 표본')
    bad = sch_raw.copy()
    allnull_idx = bad.index[bad.isna().all(axis=1)][0]
    bad.loc[allnull_idx, "route_name"] = "x"
    with pytest.raises(ContractViolation):
        raw_schedule_before.validate(bad)


def test_schedule_dup_key_rejected(sch_raw):
    # 위반 주입: (trip_id, stop_sequence) 중복 1행 (스펙 §7.2)
    bad = sch_raw.copy()
    bad.loc[bad.index[1], ["trip_id", "stop_sequence"]] = bad.loc[
        bad.index[0], ["trip_id", "stop_sequence"]].values
    # route_id까지 맞춰 완전 중복이 아닌 키 중복으로 만든다
    bad.loc[bad.index[1], "route_id"] = bad.loc[bad.index[0], "route_id"]
    with pytest.raises(ContractViolation):
        raw_schedule_before.validate(bad)
