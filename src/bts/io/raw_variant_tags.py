"""reference/variant_tagging 로더 — variant_tags.csv + evidence/ (stage1_canonical_spec.md §3.3).

(481,18)·role 8종 enum·PK 유일을 로딩과 동시에 검증한다 — 4종 로더는 로딩 즉시 실패해야 한다.
★ base_route_id는 **원형 보존** — 어떤 정규화도 하지 않는다(main 자기참조 92·circular 자기참조 34
포함 원형 그대로. 정규화는 s01 소속 — role 스코프 규칙이 canonical 379의 성립 조건, design.md §5 s01).
불변식은 감사 실측(reference/audit/variant_tags.md)의 이식 — 위반 시 ContractViolation.
아래 상수는 감사 실측 기준값이다(임계값 아님, 출처 [VT§n]).
"""
from __future__ import annotations

import json

import pandas as pd

import bts.paths as paths
from bts.io import ContractViolation

_FILE = "variant_tagging/variant_tags.csv"
_EVIDENCE_DIR = ("variant_tagging", "evidence")
_COLUMNS = ["route", "route_type", "route_id", "dir_label", "n_stops", "frequency",
            "is_loop", "first", "last", "role", "direction_group", "base_route_id",
            "confidence", "source", "verified", "verifier_agree",
            "added_or_removed", "evidence"]                     # [VT§1] 18컬럼 순서 고정
_ROWS = 481                                                     # [VT§1]
_ROLES = {"main", "circular", "short_turn", "branch",
          "detour", "extension", "duplicate", "anomaly"}        # [VT§2] 8종 — 4종 아님
_ROUTE_TYPES = {"General", "Express"}                           # [VT§1]
_CONFIDENCE = {"high", "medium", "low"}                         # [VT§1]
_SOURCES = {"agent", "auto"}                                    # [VT§1]
_BOOL_STR = {"True", "False"}                                   # [VT§1] 문자열 boolean
_ROUTE_ID_RE = r"BR_(TAGO_USB\d{9}|ACC0_\d{8})"                 # [VT§1] 20자/16자 실측
_EVIDENCE_FILES = 184                                           # [VT§5] 노선(route) 1:1


def _fail(msg: str) -> None:
    raise ContractViolation(f"variant_tags: {msg}")


def validate(df: pd.DataFrame, meta: dict | None = None) -> pd.DataFrame:
    """raw 불변식 assert. base_route_id는 원형 보존(정규화 금지)."""
    m = meta if meta is not None else {}
    if df.columns[0].startswith("﻿"):
        _fail("BOM 포함 컬럼명 — utf-8-sig 로딩 실패 [VT§1]")
    if list(df.columns) != _COLUMNS:
        _fail(f"컬럼 불일치 {list(df.columns)} [VT§1]")
    m["raw_shape"] = [int(df.shape[0]), int(df.shape[1])]
    if len(df) != _ROWS:
        _fail(f"행수 {len(df)} != {_ROWS} [VT§1]")

    if not df["route_id"].is_unique:
        _fail("route_id PK 유일성 위반 [VT§1]")
    if not df["route_id"].str.fullmatch(_ROUTE_ID_RE).all():
        _fail("route_id 프리픽스 regex 위반 [VT§1]")

    unknown_roles = sorted(set(df["role"].dropna().unique()) - _ROLES)
    if unknown_roles:
        _fail(f"role enum(8종) 밖 미지값 {unknown_roles} [VT§2]")
    if df["role"].isna().any():
        _fail("role 결측 [VT§1]")

    for col, allowed in (("route_type", _ROUTE_TYPES), ("confidence", _CONFIDENCE),
                         ("source", _SOURCES), ("is_loop", _BOOL_STR),
                         ("verified", _BOOL_STR)):
        bad = sorted(set(df[col].dropna().unique()) - allowed)
        if bad or df[col].isna().any():
            _fail(f"{col} enum 위반 또는 결측: {bad} [VT§1]")

    # 정수 파싱 가능성 (n_stops 2~165, frequency 1~104 — 값 대조는 체크 소속)
    try:
        df["n_stops"].astype(int)
        df["frequency"].astype(int)
    except ValueError as e:
        _fail(f"n_stops/frequency 정수 파싱 실패: {e}")

    # base_route_id: dangling 0 — 전부 파일 내 실존 route_id 참조 [VT§3].
    # ★ 값은 원형 보존 — 자기참조(main 92·circular 34)를 결측으로 바꾸지 않는다.
    base = df["base_route_id"].dropna()
    dangling = sorted(set(base.unique()) - set(df["route_id"]))
    if dangling:
        _fail(f"base_route_id dangling 참조 {dangling[:5]} [VT§3]")

    # source=agent ⇔ verified=True 완전 동치 [VT§1]
    agent = df["source"] == "agent"
    ver = df["verified"] == "True"
    m["source_verified_equiv_violations"] = int((agent != ver).sum())
    if m["source_verified_equiv_violations"]:
        _fail("source=agent ⇔ verified=True 동치 위반 [VT§1]")
    return df


def load(meta: dict | None = None) -> pd.DataFrame:
    """raw 로드 + 불변식 검증. meta(dict 전달 시)에 관찰값 기록."""
    df = pd.read_csv(paths.raw_path(_FILE), encoding="utf-8-sig", dtype=str)
    return validate(df, meta)


def load_evidence() -> dict[str, dict]:
    """evidence/ 184 JSON → {route: 문서}. 파일명 == 내부 route 1:1 검증 [VT§5]."""
    ev_dir = paths.REFERENCE.joinpath(*_EVIDENCE_DIR)
    files = sorted(ev_dir.glob("*.json"))
    if len(files) != _EVIDENCE_FILES:
        _fail(f"evidence 파일수 {len(files)} != {_EVIDENCE_FILES} [VT§5]")
    out: dict[str, dict] = {}
    for p in files:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        if doc.get("route") != p.stem:
            _fail(f"evidence 파일명({p.stem}) != 내부 route({doc.get('route')}) [VT§5]")
        for key in ("route", "n_variants", "variants"):
            if key not in doc:
                _fail(f"evidence {p.name}: 필수 키 {key} 부재 [VT§5]")
        out[p.stem] = doc
    return out
