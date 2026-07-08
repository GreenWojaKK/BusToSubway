"""s01_canonical / after — trip 복원 경로 (design.md §5 s01 after 1·3·4·5, spec §6).

after는 variant_tags가 없는 대신 변형이 pattern_id 레벨에서 이미 분리돼 있다
(끝자리 = 변형 코드 [SA§6.2]).

절차:
  1. trip 복원(trip_split.reconstruct_trips) — 4,524 왕복 trips [SA§5.1]
  3. canonical 조립: 정차열 기준은 s00 route_master(1:1 격자)를 그대로 사용.
     bus_route_after의 stop_sequence는 재부여 번호라 사용 금지 [SA§5.4] —
     dense-rank 94% 일치는 검증 참고 기록으로만 남는다(체크 C-S01-A-007).
  4. main 선정(경량): variant_code==0 가설 ∧ 최다 trip 규칙의 합의로 제안만 —
     불일치는 needs_review=True로 남기고 자동 확정하지 않는다(ADR-003).
  5. route_class: route_class_rules.after.yaml 적용(경성 항등 = 클래스 합 == 184).

범위 밖: 편도(leg) 분리 — ADR-002 미결. trips는 왕복(회차) 단위임이 스키마 주석에 명시되고
trip_legs.parquet은 생성하지 않는다.

출력: trips.parquet(4,524) / trip_events.parquet(280,797) / route_master.parquet(s00 파일을
바이트 동일 복사) / pattern_registry.parquet(351) / route_registry.parquet(184)
/ main_proposal.csv(184).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pandas as pd

import paths
from s01_canonical import get_rules, trip_split

# get_rules는 패키지 루트의 단일 정의를 재수출한다 — design.md §7.2 "규칙 조회 API는
# get_rules(era) 하나뿐". 같은 기능을 두 번 정의하지 않는다.

UPSTREAM = "s00_ingest"          # registry 입력 화이트리스트와 일치해야 한다
SCOPE = "after"


def _rule_matches(rule_expr: str, name: str, patterns: tuple[str, ...]) -> bool:
    """규칙 표현식 1개 평가. 미지 유형은 ValueError(조용한 오분류 차단).

    after 규칙 유형: pattern_prefix(pattern_id 접두) / in(이름 목록) /
    fullmatch(이름 정규식) / fallback(잔여 전부).
    """
    if rule_expr == "fallback":
        return True
    kind, sep, arg = rule_expr.partition(":")
    if not sep:
        raise ValueError(f"규칙 표현식 형식 위반: {rule_expr!r}")
    if kind == "pattern_prefix":
        return any(str(p).startswith(arg) for p in patterns)
    if kind == "in":
        items = {t.strip() for t in arg.strip("[]").split(",")}
        return name in items
    if kind == "fullmatch":
        return re.fullmatch(arg, name) is not None
    raise ValueError(f"미지의 규칙 유형: {rule_expr!r}")


def classify_route_names(name_patterns: dict[str, tuple[str, ...]],
                         rules: dict, era: str) -> dict[str, str]:
    """route_name → route_class. 규칙은 위에서 아래로 우선 적용(yangsan이 최우선).

    name_patterns: {route_name: 그 이름의 pattern_id들} — pattern_prefix 규칙용.
    era가 맞지 않는 규칙 적용은 KeyError로 막는다(C-S01-A-008).
    """
    if rules.get("era") != era:
        raise KeyError(
            f"era {era!r} 분류에 {rules.get('era')!r} 규칙 적용 금지 (portability: forbidden)")
    out: dict[str, str] = {}
    for name, patterns in name_patterns.items():
        for r in rules["rules"]:
            if _rule_matches(r["rule"], name, patterns):
                out[name] = r["class"]
                break
        else:
            raise ValueError(f"어느 규칙에도 걸리지 않은 이름: {name!r} (fallback 부재?)")
    return out


def boundary_clip_prefixes(rules: dict) -> tuple[str, ...]:
    """scope_flag: boundary_clipped 규칙 행에서 pattern 접두어를 추출한다."""
    out = []
    for r in rules["rules"]:
        if r.get("scope_flag") == "boundary_clipped":
            kind, _, arg = str(r["rule"]).partition(":")
            if kind != "pattern_prefix":
                raise ValueError(
                    f"boundary_clipped 규칙은 pattern_prefix여야 한다: {r['rule']!r}")
            out.append(arg)
    return tuple(out)


# ── registry 조립 ────────────────────────────────────────────────────────────
def build_registries(events: pd.DataFrame, trips: pd.DataFrame,
                     route_master: pd.DataFrame, rules: dict
                     ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """pattern_registry(351) + route_registry(184).

    - 이름미상 3 pattern은 route_name=NULL 유지 + name_unknown=True — 임의 명명 금지
      (이름은 id에서 유추 가능하나 미확정 [SA§6.2]).
    - route_class는 route_name 단위 분류 결과를 pattern에 붙인 값 — 이름미상 pattern은 NULL.
    - n_master_stops는 route_master 격자의 관측 칸 수(결번은 결측이 아니라 통과 stop
      인덱스 [SA§5.4] — '연속 정차 카운터' 가정 금지).
    """
    pat = (events.groupby("pattern_id")
                 .agg(route_name=("route_name", "first"))   # first는 non-null 우선, 전결측=NaN
                 .reset_index())
    pat["name_unknown"] = pat["route_name"].isna()
    pat["variant_code"] = pat["pattern_id"].str[-1].astype("int8")   # 끝자리 = 변형 코드

    clip = boundary_clip_prefixes(rules)
    pat["boundary_clipped"] = (pat["pattern_id"].str.startswith(tuple(clip))
                               if clip else False)

    n_trips = trips.groupby("pattern_id").size()
    pat["n_trips"] = pat["pattern_id"].map(n_trips).fillna(0).astype("int32")
    n_master = route_master.groupby("pattern_id").size()
    pat["n_master_stops"] = pat["pattern_id"].map(n_master).astype("int32")

    named = pat.dropna(subset=["route_name"])
    name_patterns = {n: tuple(g["pattern_id"]) for n, g in named.groupby("route_name")}
    cls = classify_route_names(name_patterns, rules, era=SCOPE)
    pat["route_class"] = pat["route_name"].map(cls)          # 이름미상은 NaN 유지

    route = (named.groupby("route_name")
                  .agg(n_patterns=("pattern_id", "size"), n_trips=("n_trips", "sum"))
                  .reset_index())
    route["route_class"] = route["route_name"].map(cls)
    route = route[["route_name", "route_class", "n_patterns", "n_trips"]]
    route = route.astype({"n_patterns": "int16", "n_trips": "int32"})

    pat = pat[["pattern_id", "route_name", "name_unknown", "variant_code",
               "route_class", "boundary_clipped", "n_trips", "n_master_stops"]]
    return pat, route


# ── main 제안 (ADR-003: 합의한 후보만 제안) ─────────────────────────────────
def propose_main(pattern_registry: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
    """route_name별 main 후보 이중 규칙 합의표.

    ① variant_code==0 가설 후보  ② 최다 trip 후보 — 둘이 일치할 때만 main_candidate 확정.
    불일치·vc0 부재·최다 동률은 needs_review=True로 남기고 빈 후보로 둔다.
    trip 수는 1차 사실인 trips에서 직접 집계한다(registry 집계의 재확인 겸용).
    두 규칙의 정합률은 checks.json에 리포트된다(향후 ADR-003 근거).
    """
    n_by_pat = trips.groupby("pattern_id").size()
    named = pattern_registry.dropna(subset=["route_name"])
    rows = []
    for name, g in named.groupby("route_name"):
        counts = {pid: int(n_by_pat.get(pid, 0)) for pid in g["pattern_id"]}
        vc0 = g.loc[g["variant_code"] == 0, "pattern_id"].tolist()
        top_n = max(counts.values())
        top = sorted(p for p, c in counts.items() if c == top_n)
        pid_vc0 = vc0[0] if len(vc0) == 1 else None
        n_vc0 = counts.get(pid_vc0) if pid_vc0 else None
        top_tie = len(top) > 1

        if pid_vc0 is None:
            basis = "vc0_missing" if not vc0 else "vc0_multiple"
        elif top_tie:
            basis = "tie"
        elif top[0] == pid_vc0:
            basis = "both"
        else:
            basis = "disagree"

        agree = basis == "both"
        rows.append({
            "route_name": name,
            "n_patterns": int(len(g)),
            "pattern_id_vc0": pid_vc0 or "",
            "n_trips_vc0": n_vc0 if n_vc0 is not None else "",
            "pattern_id_top": top[0] if not top_tie else "|".join(top),
            "n_trips_top": top_n,
            "top_tie": top_tie,
            "basis": basis,
            "main_candidate": pid_vc0 if agree else "",     # 합의 시에만 채운다
            "needs_review": not agree,
        })
    return pd.DataFrame(rows).sort_values("route_name").reset_index(drop=True)


# ── build 진입점 ─────────────────────────────────────────────────────────────
def build(inputs, params: dict, vdir: Path) -> dict[str, Path]:
    """s01_canonical/after 빌드 — trip 복원 + route_master 복사 + registry + main 제안."""
    up = inputs.artifacts[f"{UPSTREAM}/{SCOPE}"]
    sdir = paths.artifact_dir(UPSTREAM, SCOPE, up["version"])
    events = pd.read_parquet(sdir / "events.parquet")
    route_master = pd.read_parquet(sdir / "route_master.parquet")

    rules = get_rules(SCOPE)                       # era 명시
    clip = boundary_clip_prefixes(rules)

    trips, trip_events = trip_split.reconstruct_trips(events, clip)
    pattern_registry, route_registry = build_registries(
        events, trips, route_master, rules)
    proposal = propose_main(pattern_registry, trips)

    out: dict[str, Path] = {}
    for name, df in [("trips.parquet", trips),
                     ("trip_events.parquet", trip_events),
                     ("pattern_registry.parquet", pattern_registry),
                     ("route_registry.parquet", route_registry)]:
        p = vdir / name
        df.to_parquet(p, index=False)
        out[name] = p

    # route_master는 재산출하지 않고 s00 결과를 바이트 동일 복사한다(spec §6.2).
    # 복사 무결성은 sha256 동일성 체크(C-S01-A-007)로 확인한다.
    rm_path = vdir / "route_master.parquet"
    shutil.copyfile(sdir / "route_master.parquet", rm_path)
    out["route_master.parquet"] = rm_path

    csv_path = vdir / "main_proposal.csv"
    proposal.to_csv(csv_path, index=False, encoding="utf-8-sig")
    out["main_proposal.csv"] = csv_path
    return out
