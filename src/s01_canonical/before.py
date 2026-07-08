"""s01_canonical / before — variant_tags를 붙여 대표 노선 패턴 구성 (design.md §5 s01 before ①~⑦, stage1 spec §5).

절차: build_patterns → join_tags → resolve_base_refs → build_disposition
→ select_canonical → build_backbone → attribute_trips → detect_duplicates → build_catalog.

핵심 규칙:
- base==self→NaN 정규화는 **main 92건 한정**. circular 자기참조 34건은 base 참조로 보존한다.
  canonical 선정식은 raw 결측 기준 ``role=='circular' & base_pattern_id_raw.isna()``(66행) —
  전 role 정규화 시 circular base-isna가 100이 되어 canonical이 413≠379로 깨진다.
- route_union은 canonical 시퀀스 원천이 아니다(trip 패턴 합집합 마스터 — 교차검증 전용).
  여기서는 catalog 이름 전수(김해공항)와 무스케줄 노선의 lineage 유도에만 읽는다.
- 분류 결과표 487 전수 회계(canonical 379 + 제외 102 + support 6) — 조용히 사라지는 행을 막는다.
- 중복 정차열 4건은 노드 제거 금지 — duplicates.csv로 명시 산출(s04는 플래그만 단다).

산출물 9종: route_catalog(191) / patterns(487) / pattern_stops / pattern_disposition(487)
/ canonical_rows(379) / backbone_stops(253패턴) / trip_attribution(7,625)
/ support_attribution.csv(6) / duplicates.csv(4).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

import paths
from dataio import ContractViolation, normalize, raw_variant_tags
from s01_canonical import get_rules

# ── 구조 상수 (임계값 아님 — 포맷·enum 사실) ─────────────────────────────────
_PATTERN_KEY_HEX = 12                   # pattern_key = sha1 12자 (stage1 spec §5.1 포맷 규약)
_MAIN = "main"
_DERIVED_ROLES = ("short_turn", "branch", "detour", "extension")  # 재귀 해소 대상 [VT§3]
_SUPPORT_ROLE = "support"               # 이식본 신설 9번째 role (design.md §5 s01 ④)
_CIRCULAR = "circular"
_CIRCULAR_BASELESS = "circular_baseless"        # params.canonical_roles의 role_scope 어휘
_CIRCULAR_WITH_BASE = "circular_with_base"
_DRT_LINEAGE = "ACC0"                   # 비결정 패턴 허용 lineage — id 목록 대신 접두어 계열로 판단 [SB§3]

# patterns.parquet 스키마 (design.md §5 s01 출력 표 — 컬럼·순서 고정)
_PATTERN_COLUMNS = ["pattern_id", "pattern_key", "route", "role",
                    "base_pattern_id_raw", "base_ref", "base_ref_resolved",
                    "direction_group", "frequency", "n_stops",
                    "is_loop", "is_drt", "in_canonical", "in_backbone"]

# 분류 사유 사전 (사람 판독용 — disposition 값 자체는 role_scope×params의 순수 함수)
_REASONS = {
    "canonical": "canonical 선정식 포함: main ∪ (circular & raw base 결측) ∪ short_turn ∪ branch (design.md §5 s01 ⑥)",
    _SUPPORT_ROLE: "variant_tags에 없는 지원 패턴 — parent_route 연결, 시간층 포함 (ADR-001)",
    "excl_" + _CIRCULAR_WITH_BASE:
        "circular이며 raw base 참조 보유(자기참조 34 포함 — base 참조 보존) [VT§3,§6]",
    "excl_detour": "파생 role=detour — canonical 제외, 패턴·trip은 보존(시간층 사용)",
    "excl_extension": "파생 role=extension — canonical 제외, 패턴·trip은 보존(시간층 사용)",
    "excl_duplicate": "route 323 내부 중복 태깅 — canonical 제외 [VT§2]",
    "excl_anomaly": "고립 파편(955) — evidence 'data anomaly; defer' [VT§3]",
}


def _pattern_key(seq) -> str:
    """대표 정차열 → 결정적 pattern_key (sha1 12자 — 재실행해도 동일)."""
    return hashlib.sha1("|".join(seq).encode("utf-8")).hexdigest()[:_PATTERN_KEY_HEX]


def _support_token(rules: dict) -> str:
    """규칙 데이터에서 지원 노선 판별 토큰('지원')을 추출한다."""
    for r in rules["rules"]:
        if r["class"] == _SUPPORT_ROLE:
            kind, _, arg = str(r["rule"]).partition(":")
            if kind == "contains" and arg:
                return arg
    raise ContractViolation("route_class_rules: support 클래스의 contains 규칙 부재")


def support_parent(base: str, token: str) -> str:
    """지원 base명에서 모선 이름을 얻는다 (design.md §5 s01 ④: '13 지원2' → '13')."""
    return base.split(token)[0].strip()


# ── ① 패턴 재구성 ────────────────────────────────────────────────────────────
def build_patterns(stop_times: pd.DataFrame, evidence: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """trip별 stop 튜플 → 패턴 487 + 대표 시퀀스 (design.md §5 s01 ①).

    - 결정 패턴(TAGO 전부 + ACC0 일부): trip 정차열이 유일 → 그 시퀀스가 대표.
    - 비결정 패턴(trip별 정차열 상이): ACC0 lineage만 허용(그 외 즉시 ContractViolation).
      대표 = evidence의 합집합 순회 시퀀스(is_drt=True), distinct 집합 == schedule 합집합
      distinct 검증(울주01: 52엔트리/distinct 44 실측). trip별 실제 시퀀스는 stop_times가
      그대로 보존된다.
    - pattern_stops는 시퀀스 내 stop 중복을 허용한다(55건 실측 — 유일성 assert 금지).
    반환: (pat_core[pattern_id, pattern_key, route_name, lineage, n_trips, rep_len, is_drt],
           pattern_stops[pattern_id, seq, stop_id])
    """
    ev_ids = {v["route_id"]: list(v["stop_ids"])
              for doc in evidence.values() for v in doc["variants"]}

    sd = stop_times.sort_values(["trip_id", "seq"], kind="mergesort")
    trip_pat = sd.groupby("trip_id")["stop_id"].agg(tuple)
    head = sd.drop_duplicates("trip_id").set_index("trip_id")[["pattern_id", "route_name", "lineage"]]
    per = head.assign(pat=trip_pat)
    grp = per.groupby("pattern_id")
    uniq_seqs = grp["pat"].agg(lambda x: sorted(set(x)))
    meta = grp.agg(route_name=("route_name", "first"),
                   lineage=("lineage", "first"), n_trips=("pat", "size"))

    rows, stop_frames = [], []
    for pid in sorted(uniq_seqs.index):
        seqs = uniq_seqs[pid]
        is_drt = len(seqs) > 1
        if is_drt:
            if meta.at[pid, "lineage"] != _DRT_LINEAGE:
                raise ContractViolation(
                    f"비결정 정차열 패턴이 {_DRT_LINEAGE} lineage 밖에서 발생: {pid} "
                    f"(TAGO 패턴 결정성 위반 [SB§3])")
            if pid not in ev_ids:
                raise ContractViolation(f"비결정 패턴 {pid}의 evidence 대표 시퀀스 부재 [VT§5]")
            rep = tuple(ev_ids[pid])
            union = set().union(*(set(s) for s in seqs))
            if set(rep) != union:
                raise ContractViolation(
                    f"비결정 패턴 {pid}: evidence 합집합 순회 distinct != schedule 합집합 distinct")
        else:
            rep = seqs[0]
        rows.append({"pattern_id": pid, "pattern_key": _pattern_key(rep),
                     "route_name": meta.at[pid, "route_name"],
                     "lineage": meta.at[pid, "lineage"],
                     "n_trips": int(meta.at[pid, "n_trips"]),
                     "rep_len": len(rep), "is_drt": is_drt})
        stop_frames.append(pd.DataFrame({
            "pattern_id": pid,
            "seq": np.arange(1, len(rep) + 1, dtype="int16"),
            "stop_id": list(rep)}))

    pat_core = pd.DataFrame(rows)
    pattern_stops = pd.concat(stop_frames, ignore_index=True)
    return pat_core, pattern_stops


# ── ② variant_tags 조인 ─────────────────────────────────────────────────────
def join_tags(pat_core: pd.DataFrame, vt: pd.DataFrame, support_token: str) -> pd.DataFrame:
    """pattern_id 키 좌조인 — 조인 방향을 확인한다 (design.md §5 s01 ②).

    ① tags 측 전건 매치(vt에만 있는 pattern 0 — 위반 시 ContractViolation).
    ② patterns 측 미태깅은 이름 규칙상 전부 지원 패턴이어야 한다(그 외 즉시 ContractViolation;
      정확히 6패턴 목록과의 exact 대조는 C-S01-B-002 소속).
    미태깅 행은 role='support'(9번째 role)로 채우고 route는 base명으로 귀속한다.
    """
    unmatched = sorted(set(vt["pattern_id"]) - set(pat_core["pattern_id"]))
    if unmatched:
        raise ContractViolation(
            f"variant_tags 조인 방향 위반: tags 측 미매치 {len(unmatched)}건 {unmatched[:5]} [VT§4]")

    j = pat_core.merge(vt, on="pattern_id", how="left")
    untagged = j["role"].isna()
    non_support = j.loc[untagged & ~j["route_name"].str.contains(support_token, regex=False),
                        "pattern_id"].tolist()
    if non_support:
        raise ContractViolation(
            f"지원 패턴이 아닌 미태깅 패턴 발생: {non_support[:5]} — variant_tags 커버리지 확인 실패 [VT§4]")

    j.loc[untagged, "role"] = _SUPPORT_ROLE
    base = j["route_name"].map(normalize.base_route_name)
    j["route"] = j["route"].where(~untagged, base)
    # 지원 행의 n_stops/frequency는 schedule 실측으로 채운다(tags 부재 — frequency==trip수는 s00 실측)
    j["n_stops"] = j["n_stops"].where(~untagged, j["rep_len"]).astype("int32")
    j["frequency"] = j["frequency"].where(~untagged, j["n_trips"]).astype("int32")
    j["is_loop"] = j["is_loop"].astype("boolean")   # 지원 행은 tags 부재 — NA 보존
    return j


# ── ③ base 참조 정규화 — role_scope가 canonical 379의 성립 조건 ──────────────
def resolve_base_refs(tagged: pd.DataFrame, max_depth: int) -> pd.DataFrame:
    """base 정규화(main 한정) + canonical 조상 재귀 해소 (design.md §5 s01 ③, spec §5.1).

    - base_pattern_id_raw: 원형 보존(결측 163).
    - base_ref: **role=='main' & base==self 만** NaN('no base'의 표기 변형 [VT§3]).
      circular 자기참조 34건은 raw 그대로 복사 — base 참조 보존이 canonical 379의 성립 조건.
      전 role 정규화 금지.
    - base_ref_resolved: **파생 role(short_turn/branch/detour/extension)만** 재귀 해소 대상
      (spec §5.1). 종착 조건은 canonical 조상 = main 또는 **base 없는 circular** —
      'role in {main, circular}'로 두면 비canonical인 circular_with_base가 종착으로
      허용되는 spec보다 약한 술어가 된다(검증 라운드 1 지적). 재해소 실측 4건
      (체인 147 + detour→branch 2 + detour→short_turn 1). max_depth 가드,
      해소 후 dangling 0(위반 시 ContractViolation). 비파생 role은 base_ref 그대로 복사.
    """
    df = tagged.copy()
    raw = df["base_pattern_id_raw"]
    main_self = (df["role"] == _MAIN) & raw.notna() & (raw == df["pattern_id"])
    df["base_ref"] = raw.where(~main_self)

    role_of = df.set_index("pattern_id")["role"]
    raw_of = df.set_index("pattern_id")["base_pattern_id_raw"]
    ref_of = df.set_index("pattern_id")["base_ref"]

    def _is_canonical_ancestor(pid) -> bool:
        """canonical 조상 술어 — main 또는 base 없는 circular (spec §5.1)."""
        r = role_of.get(pid)
        return r == _MAIN or (r == _CIRCULAR and pd.isna(raw_of.get(pid)))

    def _resolve(start):
        cur, depth = start, 0
        while True:
            r = role_of.get(cur)
            if r is None:
                raise ContractViolation(f"base 참조 dangling: {cur} (파일 내 실존 참조 검증 규칙 위반 [VT§3])")
            if _is_canonical_ancestor(cur):
                return cur
            depth += 1
            if depth > max_depth:
                raise ContractViolation(
                    f"base 재귀 해소 depth 초과(>{max_depth}): {start} — 순환/과深 체인 의심")
            nxt = ref_of.get(cur)
            if nxt is None or pd.isna(nxt):
                raise ContractViolation(
                    f"canonical 조상 부재: {start} → {cur}(role={r})의 base 결측")
            cur = nxt

    derived = df["role"].isin(_DERIVED_ROLES)
    df["base_ref_resolved"] = df["base_ref"]
    df.loc[derived, "base_ref_resolved"] = df.loc[derived, "base_ref"].map(
        lambda b: _resolve(b) if pd.notna(b) else np.nan)
    return df


# ── ⑤ 분류 결과표(전수 회계) + 역할 파생 집합 플래그 ────────────────────────
def build_disposition(resolved: pd.DataFrame, canonical_roles: list,
                      backbone_roles: list) -> pd.DataFrame:
    """role_scope 파생 + disposition 부여 — 전건 상호 배타·전수 커버 (design.md §5 s01 ⑤⑥).

    role_scope: circular를 raw base 결측 기준으로 circular_baseless/with_base로 분해
    (그 외 role은 그대로). in_canonical/in_backbone은 params 목록의 순수 함수.
    제외 role은 배제가 아니라 보존 — 패턴·trip이 역할과 함께 남아 시간층이 사용한다.
    """
    df = resolved.copy()
    circ = df["role"] == _CIRCULAR
    df["role_scope"] = df["role"].where(
        ~circ, np.where(df["base_pattern_id_raw"].isna(),
                        _CIRCULAR_BASELESS, _CIRCULAR_WITH_BASE))
    df["in_canonical"] = df["role_scope"].isin(canonical_roles)
    df["in_backbone"] = df["role_scope"].isin(backbone_roles)

    disp = "excl_" + df["role_scope"]
    disp = disp.mask(df["in_canonical"], "canonical")
    disp = disp.mask(df["role"] == _SUPPORT_ROLE, _SUPPORT_ROLE)
    df["disposition"] = disp
    unknown = sorted(set(df["disposition"]) - set(_REASONS))
    if unknown:
        raise ContractViolation(f"disposition 사유 사전 밖 값 발생: {unknown} — 전수 커버 위반")
    df["reason"] = df["disposition"].map(_REASONS)
    return df


# ── ⑥ backbone (장소 그래프 입력) ───────────────────────────────────────────
def build_backbone(patterns: pd.DataFrame, pattern_stops: pd.DataFrame) -> pd.DataFrame:
    """main ∪ base 없는 circular 패턴의 방향 정차열."""
    bb = patterns.loc[patterns["in_backbone"],
                      ["pattern_id", "route", "direction_group"]]
    out = bb.merge(pattern_stops, on="pattern_id", how="left")
    return out[["route", "direction_group", "pattern_id", "seq", "stop_id"]].reset_index(drop=True)


# ── ④ trip 전건 연결 (지원 포함 100%) ───────────────────────────────────────
def attribute_trips(trips: pd.DataFrame, patterns: pd.DataFrame,
                    support_token: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """trip 7,625 전건: pattern_id, route, role, attribution ∈ {tagged, support}.

    support 57 trips는 base명 규칙으로 parent_route(13/236/802/924)에 연결한다(ADR-001).
    parent가 정규 route 목록에 없으면 ContractViolation.
    반환: (trip_attribution, support_attribution — 사람 검토용 6행)
    """
    pm = patterns.set_index("pattern_id")
    ta = trips[["trip_id", "pattern_id"]].copy()
    ta["role"] = ta["pattern_id"].map(pm["role"])
    sup_pids = pm.index[pm["role"] == _SUPPORT_ROLE]
    parent = {pid: support_parent(pm.at[pid, "route"], support_token) for pid in sup_pids}
    regular_routes = set(pm.loc[pm["role"] != _SUPPORT_ROLE, "route"])
    bad = sorted(v for v in parent.values() if v not in regular_routes)
    if bad:
        raise ContractViolation(f"지원 parent_route가 정규 route 목록에 부재: {bad} (ADR-001)")

    sup = ta["role"] == _SUPPORT_ROLE
    ta["route"] = ta["pattern_id"].map(pm["route"]).where(~sup, ta["pattern_id"].map(parent))
    ta["attribution"] = np.where(sup, "support", "tagged")
    ta = (ta[["trip_id", "pattern_id", "route", "role", "attribution"]]
          .sort_values("trip_id", kind="mergesort").reset_index(drop=True))

    support_tab = pd.DataFrame(
        [{"pattern_id": pid, "route_name": pm.at[pid, "route_name"],
          "base": pm.at[pid, "route"], "parent_route": parent[pid],
          "n_trips": int(pm.at[pid, "n_trips"])} for pid in sorted(sup_pids)])
    return ta, support_tab


# ── ⑦ 중복 정차열 명시 처리 (노드 제거 금지) ─────────────────────────────────
def detect_duplicates(patterns: pd.DataFrame) -> pd.DataFrame:
    """pattern_key 충돌 중 route가 다른 것 — duplicates.csv (기본 policy=keep_flag).

    s04는 노드를 유지하고 쌍에 is_duplicate_pair 플래그만 단다
    (M=C(170,2) 정합 조건). variant_tags role=duplicate 2행(route 323 내부)과는 별개 사실.
    """
    rows = []
    for key, g in patterns.groupby("pattern_key"):
        if g["route"].nunique() > 1:
            rows.append({"pattern_key": key,
                         "pattern_ids": "|".join(sorted(g["pattern_id"])),
                         "routes": "|".join(sorted(g["route"].unique())),
                         "n_patterns": len(g),
                         "policy": "keep_flag"})
    return (pd.DataFrame(rows, columns=["pattern_key", "pattern_ids", "routes",
                                        "n_patterns", "policy"])
            .sort_values("pattern_key", kind="mergesort").reset_index(drop=True))


# ── catalog: base 노선 191 + route_class (규칙은 데이터) ─────────────────────
def classify_routes(catalog_base: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """route_class 부여 + expect_count·accounting 자가 검증 (design.md §7.2).

    catalog_base: route(base) 단위 프레임 (route, has_schedule, lineage, n_patterns, n_trips).
    규칙 파일과 관측 개수가 어긋나면 즉시 ContractViolation을 낸다.
    """
    def _match(rule: str, base: str) -> bool:
        if rule == "fallback":
            return True
        kind, _, arg = rule.partition(":")
        if kind == "fullmatch":
            import re
            return re.fullmatch(arg, base) is not None
        if kind == "contains":
            return arg in base
        if kind == "equals":
            return base == arg
        raise ContractViolation(f"route_class_rules: 미지 규칙 형식 '{rule}'")

    era = rules["era"]
    cls, rid = [], []
    for base in catalog_base["route"]:
        for r in rules["rules"]:
            if _match(str(r["rule"]), str(base)):
                cls.append(r["class"])
                rid.append(f"{era}:{r['class']}")
                break
        else:
            raise ContractViolation(f"어느 규칙에도 걸리지 않는 base: {base} (fallback 부재?)")
    out = catalog_base.copy()
    out["route_class"] = cls
    out["rule_id"] = rid

    counts = out["route_class"].value_counts().to_dict()
    for r in rules["rules"]:
        exp = r.get("expect_bases")
        if exp is not None and counts.get(r["class"], 0) != exp:
            raise ContractViolation(
                f"expect_count 위반: class={r['class']} 관측 {counts.get(r['class'], 0)} != 기대 {exp} "
                f"(규칙 변경 확인 — design.md §7.2)")
    acc = rules.get("accounting", {})
    scope_out = {r["class"] for r in rules["rules"] if r.get("scope_out")}
    regular = sum(n for c, n in counts.items() if c not in scope_out)
    if "regular_bases" in acc and regular != acc["regular_bases"]:
        raise ContractViolation(f"accounting 위반: 정규 base {regular} != {acc['regular_bases']}")
    if "catalog_bases" in acc and len(out) != acc["catalog_bases"]:
        raise ContractViolation(f"accounting 위반: catalog {len(out)} != {acc['catalog_bases']}")
    return out[["route", "route_class", "has_schedule", "lineage",
                "n_patterns", "n_trips", "rule_id"]]


def build_catalog(patterns: pd.DataFrame, route_union: pd.DataFrame,
                  stops: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """catalog 191 = schedule base 190 + 김해공항 (design.md §5 s01 출력).

    '50(내고산 방면)'은 base 50에 흡수되어 base 행을 늘리지 않는다.
    스케줄이 없는 노선의 lineage는 route_union stop들의 lineage에서 유도한다.
    """
    sch = patterns.groupby("route").agg(
        n_patterns=("pattern_id", "size"), n_trips=("n_trips", "sum"),
        lineage=("lineage", lambda x: "|".join(sorted(set(x)))))
    ru_base = route_union["route_name"].map(normalize.base_route_name)
    all_routes = sorted(set(sch.index) | set(ru_base.unique()))
    lin_stop = stops.set_index("stop_id")["lineage"]

    rows = []
    for r in all_routes:
        if r in sch.index:
            rows.append({"route": r, "has_schedule": True,
                         "lineage": sch.at[r, "lineage"],
                         "n_patterns": int(sch.at[r, "n_patterns"]),
                         "n_trips": int(sch.at[r, "n_trips"])})
        else:
            lin = "|".join(sorted(route_union.loc[ru_base == r, "stop_id"]
                                  .map(lin_stop).dropna().unique()))
            rows.append({"route": r, "has_schedule": False, "lineage": lin,
                         "n_patterns": 0, "n_trips": 0})
    base_df = pd.DataFrame(rows)
    base_df["n_patterns"] = base_df["n_patterns"].astype("int32")
    base_df["n_trips"] = base_df["n_trips"].astype("int32")
    return classify_routes(base_df, rules)


# ── 산출 헬퍼 ────────────────────────────────────────────────────────────────
def _write_parquet(df: pd.DataFrame, path: Path) -> Path:
    paths.assert_writable(path)
    df.to_parquet(path, index=False)
    return path


def _write_csv(df: pd.DataFrame, path: Path) -> Path:
    paths.assert_writable(path)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ── 빌드 진입점 ──────────────────────────────────────────────────────────────
def build(inputs, params: dict, vdir: Path) -> dict[str, Path]:
    """s00 산출물 + variant_tags evidence + 규칙 데이터 → canonical 노선 패턴 구성."""
    vdir = Path(vdir)
    s00_ver = inputs.artifacts["s00_ingest/before"]["version"]
    s00 = paths.artifact_dir("s00_ingest", "before", s00_ver)

    stop_times = pd.read_parquet(s00 / "stop_times.parquet")
    trips = pd.read_parquet(s00 / "trips.parquet")
    stops = pd.read_parquet(s00 / "stops.parquet")
    route_union = pd.read_parquet(s00 / "route_union.parquet")   # 교차검증·catalog 전용
    vt = pd.read_parquet(s00 / "variant_tags.parquet")
    evidence = raw_variant_tags.load_evidence()
    rules = get_rules("before")
    token = _support_token(rules)

    # ① 패턴 재구성 → ② variant_tags 조인 → ③ base 정규화·해소 → ⑤⑥ 분류·플래그
    pat_core, pattern_stops = build_patterns(stop_times, evidence)
    tagged = join_tags(pat_core, vt, token)
    resolved = resolve_base_refs(tagged, max_depth=params["base_ref_max_depth"])
    patterns = build_disposition(resolved, params["canonical_roles"], params["backbone_roles"])

    canonical = patterns[patterns["in_canonical"]]           # select_canonical (379 뷰)
    backbone_stops = build_backbone(patterns, pattern_stops)
    ta, support_tab = attribute_trips(trips, patterns, token)  # ④ 귀속 100%
    duplicates = detect_duplicates(patterns)                   # ⑦ 중복 명시
    catalog = build_catalog(patterns, route_union, stops, rules)

    disposition = patterns[["pattern_id", "route", "route_name", "role", "role_scope",
                            "disposition", "reason", "base_ref_resolved", "n_trips"]].copy()
    disposition["n_trips"] = disposition["n_trips"].astype("int32")

    out = {
        "route_catalog.parquet": _write_parquet(catalog, vdir / "route_catalog.parquet"),
        "patterns.parquet": _write_parquet(patterns[_PATTERN_COLUMNS], vdir / "patterns.parquet"),
        "pattern_stops.parquet": _write_parquet(pattern_stops, vdir / "pattern_stops.parquet"),
        "pattern_disposition.parquet": _write_parquet(disposition, vdir / "pattern_disposition.parquet"),
        "canonical_rows.parquet": _write_parquet(canonical[_PATTERN_COLUMNS].reset_index(drop=True),
                                                 vdir / "canonical_rows.parquet"),
        "backbone_stops.parquet": _write_parquet(backbone_stops, vdir / "backbone_stops.parquet"),
        "trip_attribution.parquet": _write_parquet(ta, vdir / "trip_attribution.parquet"),
        "support_attribution.csv": _write_csv(support_tab, vdir / "support_attribution.csv"),
        "duplicates.csv": _write_csv(duplicates, vdir / "duplicates.csv"),
    }
    return out
