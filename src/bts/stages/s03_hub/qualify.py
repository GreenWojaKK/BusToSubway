"""s03_hub hub_class 판정 — params의 순수 함수 (stage2_place_hub_spec.md §5.3, design.md §5 s03).

지표(place_metrics)와 판정(hub_qualification)의 2파일 분리 중 판정 쪽:
재판정은 재계산 없이 params(thresholds) 변경만으로 초 단위다.
임계값은 전부 params.stages.s03_hub.thresholds — 코드 내 수치 리터럴 없음.
hub_class의 NONE은 universe·params 상대적 판정이다(ADR-010 결정 4 — "환승 불가" 판정 아님).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import bts.paths as paths
from bts.io import ContractViolation

HUB_CLASSES = ("CROSSING", "TERMINAL", "NONE")
OVERRIDE_COLUMNS = ["place_id", "hub_class", "reason", "source"]
QUALIFICATION_COLUMNS = ["place_id", "hub_class", "hub_class_rule",
                         "is_crossing", "is_terminal", "is_override", "override_row"]
THRESHOLD_KEYS = ("crossing_min_degree", "crossing_min_arms",
                  "terminal_min_degree", "terminal_max_arms", "terminal_min_lstar")


def qualify(metrics: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    """지표 → hub_class의 순수 함수 (spec §5.3).

    is_crossing = (D >= crossing_min_degree) & (A >= crossing_min_arms)
    is_terminal = (D >= terminal_min_degree) & (A <= terminal_max_arms) & (L_star >= terminal_min_lstar)
    hub_class_rule = CROSSING if is_crossing else (TERMINAL if is_terminal else NONE)
    — 기본 임계에서 두 술어는 A 조건으로 상호 배타(crossing_min_arms > terminal_max_arms).
    임계 변경으로 겹치면 CROSSING 우선(체크 C-S03-B-009가 note로 기록).
    metrics 요구 컬럼: place_id, D, A, L_star.
    """
    missing = [k for k in THRESHOLD_KEYS if k not in thresholds]
    if missing:
        raise ContractViolation(f"thresholds 키 누락: {missing} — params.stages.s03_hub.thresholds")
    d = metrics["D"].astype(int)
    a = metrics["A"].astype(int)
    ls = metrics["L_star"].astype(int)
    is_crossing = (d >= int(thresholds["crossing_min_degree"])) \
        & (a >= int(thresholds["crossing_min_arms"]))
    is_terminal = (d >= int(thresholds["terminal_min_degree"])) \
        & (a <= int(thresholds["terminal_max_arms"])) \
        & (ls >= int(thresholds["terminal_min_lstar"]))
    rule = pd.Series("NONE", index=metrics.index)
    rule[is_terminal] = "TERMINAL"
    rule[is_crossing] = "CROSSING"          # 겹침 시 CROSSING 우선 (나중 대입이 이김)
    out = pd.DataFrame({
        "place_id": metrics["place_id"].astype(str),
        "hub_class_rule": rule,
        "is_crossing": is_crossing.astype(bool),
        "is_terminal": is_terminal.astype(bool),
    })
    return out.reset_index(drop=True)


def load_hub_overrides(path: Path | None = None) -> pd.DataFrame:
    """config/overrides/hub_overrides.csv — 헤더만 있는 빈 파일도 허용한다."""
    p = Path(path) if path is not None else (paths.CONFIG / "overrides" / "hub_overrides.csv")
    df = pd.read_csv(p, encoding="utf-8-sig", dtype=str)
    if list(df.columns) != OVERRIDE_COLUMNS:
        raise ContractViolation(
            f"hub_overrides.csv 헤더 위반: {list(df.columns)} != {OVERRIDE_COLUMNS}")
    return df


def apply_hub_overrides(qualification: pd.DataFrame,
                        overrides: pd.DataFrame) -> pd.DataFrame:
    """수동 hub_class 판정을 적용한다(spec §5.3) — hub_qualification 최종 스키마 조립.

    - hub_class enum {CROSSING, TERMINAL, NONE} 밖 → ContractViolation.
    - place_id 참조 불능 → ContractViolation.
    - 적용 행은 is_override=True + override_row(1-기반 파일 행 번호).
      hub_class_rule(순수 함수 결과)은 별도 보존 — override가 무엇을 덮었는지 diff 가능.
    - 행이 실리면 --reviewed-by 확인은 러너(registry review_overrides)가 담당한다.
    """
    q = qualification.copy()
    q["hub_class"] = q["hub_class_rule"]
    q["is_override"] = False
    q["override_row"] = pd.array([None] * len(q), dtype="Int16")
    index_of = {pid: i for i, pid in enumerate(q["place_id"])}
    for row_no, row in enumerate(overrides.itertuples(index=False), start=1):
        cls = str(row.hub_class)
        if cls not in HUB_CLASSES:
            raise ContractViolation(
                f"hub_overrides {row_no}행: 미지 hub_class '{cls}' (허용: {list(HUB_CLASSES)})")
        pid = str(row.place_id)
        if pid not in index_of:
            raise ContractViolation(f"hub_overrides {row_no}행: 참조 불능 place_id '{pid}'")
        i = index_of[pid]
        q.loc[i, "hub_class"] = cls
        q.loc[i, "is_override"] = True
        q.loc[i, "override_row"] = row_no
    return q[QUALIFICATION_COLUMNS].reset_index(drop=True)
