"""s03_hub / before — place-level L-space 구축과 지표 계산
(design.md §5 s03, stage2_place_hub_spec.md §5, ADR-008/009/010).

절차: map_to_places → build_lspace → compute_degree → compute_arms(ADR-009)
→ compute_lifetime(ADR-008) → assemble_metrics(전량) → qualify(qualify.py)
→ build_sensitivity → annotate_gap(ADR-010) → 산출.

구조적 불변식:
- place_metrics는 **place 전량 1:1**이다. D=0은 "환승 잠재 없음"이 아니라
  채택한 그래프에서 관측된 연결 증거가 없다는 뜻이다(ADR-010).
- L-space = backbone × General universe. patterns/pattern_stops는 D=0 사유 설명용이며
  그래프 구축에 쓰이지 않는다(spec §5.5 — annotate_gap만 소비).
- 임계·격자는 전부 params(stages.s03_hub) — 코드 내 수치 임계 리터럴 없음.
- override 검토 승인(--reviewed-by)은 러너 소관(registry review_overrides).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import bts.paths as paths
from bts.io import ContractViolation, normalize
from bts.stages.s03_hub import qualify as qualify_mod

# universe 이름 → route_class 집합 (정의 상수 — s04와 동일 규약, spec §5.1·§7.2)
UNIVERSE_ROUTE_CLASSES = {"general_before": ("general", "ulju")}

MASK_MODES = ("masked_global", "subgraph")
DOMINANCES = ("weak", "strict")
_DOM_VARIANTS = tuple((m, d) for m in MASK_MODES for d in DOMINANCES)

EDGE_COLUMNS = ["place_a", "place_b", "n_patterns", "n_routes", "routes", "gap_m"]
METRIC_COLUMNS = ["place_id", "name_norm", "D", "A", "L", "L_star", "in_lspace"]
GAP_COLUMNS = ["place_id", "name_norm", "reason", "routes_serving"]
SENSITIVITY_COLUMNS = ["variant_id", "mask_mode", "dominance", "k_max", "arm_theta_deg",
                       "n_crossing", "n_terminal", "n_hub_qualified", "is_adopted"]

# D=0 사유 enum (ADR-010 / spec §5.5). backbone_visible_only는 예상 밖 케이스를 드러내는 값
# (universe backbone 패턴이 서비스하는데 D=0 — 패턴이 단일 place로 붕괴한 경우에만 가능).
GAP_REASONS = ("uncovered_circular_only", "out_of_universe_only",
               "non_backbone_variant_only", "no_pattern", "mixed_nonbackbone",
               "backbone_visible_only")


# ── universe ─────────────────────────────────────────────────────────────────
def universe_route_set(route_catalog: pd.DataFrame, universe: str) -> set[str]:
    """params.universe 이름 → route 집합. 알 수 없는 universe는 ContractViolation."""
    if universe not in UNIVERSE_ROUTE_CLASSES:
        raise ContractViolation(
            f"미지 universe '{universe}' (허용: {sorted(UNIVERSE_ROUTE_CLASSES)})")
    classes = set(UNIVERSE_ROUTE_CLASSES[universe])
    return set(route_catalog.loc[route_catalog["route_class"].isin(classes), "route"]
               .astype(str))


# ── L-space 구축 ─────────────────────────────────────────────────────────────
def build_lspace(backbone: pd.DataFrame, stop2place: pd.Series,
                 universe_routes: set, places: pd.DataFrame) -> pd.DataFrame:
    """l_space_place_edges (spec §5.2).

    패턴별 seq 정렬 → stop열을 place열로 사상 → 연속 동일 place 붕괴(자기루프 제거의
    유일 규칙) → 인접 상이 place 쌍마다 무향 엣지(place_id 사전순 a<b) → dedup + 집계.
    비연속 재방문(순환 폐합 등)은 그대로 별도 엣지.
    """
    bb = backbone[backbone["route"].astype(str).isin(universe_routes)].copy()
    bb = bb.sort_values(["pattern_id", "seq"], kind="mergesort")
    bb["place_id"] = bb["stop_id"].map(stop2place)
    if bb["place_id"].isna().any():
        missing = sorted(bb.loc[bb["place_id"].isna(), "stop_id"].unique())[:10]
        raise ContractViolation(
            f"backbone stop의 place 사상 실패 {int(bb['place_id'].isna().sum())}건 "
            f"(표본 {missing}) — stop_place_map에 모든 stop이 있어야 함")

    prev_place = bb.groupby("pattern_id")["place_id"].shift()
    bb = bb[bb["place_id"] != prev_place]           # 연속 동일 place 붕괴 (첫 행은 NaN≠place로 보존)
    prev = bb.groupby("pattern_id")[["place_id"]].shift()
    pairs = pd.DataFrame({
        "u": prev["place_id"], "v": bb["place_id"],
        "pattern_id": bb["pattern_id"], "route": bb["route"].astype(str),
    }).dropna(subset=["u"])
    pairs["place_a"] = np.where(pairs["u"] < pairs["v"], pairs["u"], pairs["v"])
    pairs["place_b"] = np.where(pairs["u"] < pairs["v"], pairs["v"], pairs["u"])

    cent = places.set_index("place_id")[["lat_centroid", "lon_centroid"]].astype(float)
    rows = []
    for (a, b), g in pairs.groupby(["place_a", "place_b"], sort=True):
        routes = sorted(g["route"].unique())
        gap = float(normalize.haversine_m(
            cent.at[a, "lat_centroid"], cent.at[a, "lon_centroid"],
            cent.at[b, "lat_centroid"], cent.at[b, "lon_centroid"]))
        rows.append((a, b, int(g["pattern_id"].nunique()), len(routes),
                     "|".join(routes), gap))
    edges = pd.DataFrame(rows, columns=EDGE_COLUMNS)
    edges["n_patterns"] = edges["n_patterns"].astype("int16")
    edges["n_routes"] = edges["n_routes"].astype("int16")
    edges["gap_m"] = edges["gap_m"].astype("float64")
    return edges.reset_index(drop=True)


def _adjacency(edges: pd.DataFrame) -> dict[str, set]:
    adj: dict[str, set] = {}
    for a, b in zip(edges["place_a"], edges["place_b"]):
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def compute_degree(edges: pd.DataFrame, places: pd.DataFrame) -> pd.Series:
    """D = 단순 그래프 이웃 place 수 — places 전량(결측=0)."""
    adj = _adjacency(edges)
    ids = places["place_id"].astype(str)
    return pd.Series([len(adj.get(p, ())) for p in ids],
                     index=pd.Index(ids, name="place_id"), name="D")


# ── arm (ADR-009) ────────────────────────────────────────────────────────────
def _bearing_deg(lat1, lon1, lat2, lon2):
    """구면 초기 방위각 [0°, 360°) — 스칼라/ndarray 겸용.

    centroid 일치(모두 0) 이웃은 atan2(0,0)=0° — 같은 팔로 흡수(ADR-009 결정 5).
    """
    p1, l1, p2, l2 = (np.radians(np.asarray(x, dtype=float))
                      for x in (lat1, lon1, lat2, lon2))
    dl = l2 - l1
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    deg = np.degrees(np.arctan2(y, x)) % 360.0
    return float(deg) if np.ndim(deg) == 0 else deg


def compute_arms(edges: pd.DataFrame, places: pd.DataFrame, theta_deg: float) -> pd.Series:
    """A = 이웃 방위각의 원형 gap(>= theta) 절단 수 (ADR-009).

    D==0 → A=0, D==1 → A=1 (단일 방위각의 wrap gap 360 >= theta로 자연 귀결).
    항등: D>=1 → 1 <= A <= D (C-S03-B-004/006).
    """
    theta = float(theta_deg)
    adj = _adjacency(edges)
    cent = places.set_index("place_id")[["lat_centroid", "lon_centroid"]].astype(float)
    out = {}
    for pid in places["place_id"].astype(str):
        nbrs = sorted(adj.get(pid, ()))
        if not nbrs:
            out[pid] = 0
            continue
        b = _bearing_deg(cent.at[pid, "lat_centroid"], cent.at[pid, "lon_centroid"],
                         cent.loc[nbrs, "lat_centroid"].to_numpy(),
                         cent.loc[nbrs, "lon_centroid"].to_numpy())
        b = np.sort(np.atleast_1d(b))
        gaps = np.diff(b, append=b[0] + 360.0)      # 원형 인접 gap (wrap 포함)
        out[pid] = max(1, int((gaps >= theta).sum()))
    ids = places["place_id"].astype(str)
    return pd.Series([out[p] for p in ids],
                     index=pd.Index(ids, name="place_id"), name="A")


# ── lifetime (ADR-008) ───────────────────────────────────────────────────────
def dominance_tables(adj: dict, D: pd.Series, k_cap: int) -> dict:
    """D>=1 place 전량의 k=1..k_cap dominance 판정표 — 4 변형(mask×dominance) 동시 계산.

    ego_k(p) = p 포함 k-hop BFS 도달 집합. masked_global = 전역 D를 ego 마스크 안 비교,
    subgraph = ego 유도 부분그래프 내 차수 비교. weak >=, strict >.
    컴포넌트 소진(frontier 공집합) 후에는 ego 불변 → 판정 고정(컴포넌트 지배자 L=K).
    반환: {place_id: {(mask, dom): [bool]*k_cap}}.
    """
    dmap = {p: int(v) for p, v in D.items()}
    tables: dict = {}
    for p, dp in dmap.items():
        if dp == 0:
            continue
        ego = {p}
        frontier = [p]
        deg_sub = {p: 0}
        dmax_global = -1        # max over ego−{p}
        dmax_sub = -1
        verdicts: dict = {v: [] for v in _DOM_VARIANTS}
        for _k in range(1, k_cap + 1):
            if frontier:
                new = sorted({v for u in frontier for v in adj.get(u, ()) if v not in ego})
                for v in new:               # 순차 편입 — 엣지당 정확 1회 계상(v 미편입 시점)
                    deg_sub[v] = 0
                    for w in adj.get(v, ()):
                        if w in ego:
                            deg_sub[v] += 1
                            deg_sub[w] += 1
                            if w != p:
                                dmax_sub = max(dmax_sub, deg_sub[w])
                    ego.add(v)
                    dmax_global = max(dmax_global, dmap.get(v, 0))
                    dmax_sub = max(dmax_sub, deg_sub[v])
                frontier = new
            verdicts[("masked_global", "weak")].append(dp >= dmax_global)
            verdicts[("masked_global", "strict")].append(dp > dmax_global)
            ds = deg_sub[p]
            verdicts[("subgraph", "weak")].append(ds >= dmax_sub)
            verdicts[("subgraph", "strict")].append(ds > dmax_sub)
        tables[p] = verdicts
    return tables


def compute_lifetime(edges: pd.DataFrame, places: pd.DataFrame, D: pd.Series,
                     mask_mode: str, dominance: str, k_max: int,
                     tables: dict | None = None) -> pd.Series:
    """L(p) = dominance(p,k)가 성립하는 최대 k ∈ [1, k_max] (미성립 0; D==0 → 0) — ADR-008.

    변형 3축(mask_mode/dominance/k_max)은 같은 함수의 인자다(sensitivity가 재사용).
    tables 주입 시 재계산 생략(빌더가 k_cap = 격자 최대 k로 1회 계산해 공유).
    """
    if mask_mode not in MASK_MODES:
        raise ContractViolation(f"미지 mask_mode '{mask_mode}' (허용: {list(MASK_MODES)})")
    if dominance not in DOMINANCES:
        raise ContractViolation(f"미지 dominance '{dominance}' (허용: {list(DOMINANCES)})")
    k_max = int(k_max)
    if tables is None:
        tables = dominance_tables(_adjacency(edges), D, k_max)
    key = (mask_mode, dominance)
    out = {}
    for pid, d in D.items():
        if int(d) == 0:
            out[pid] = 0
            continue
        flags = tables[pid][key][:k_max]
        out[pid] = max((k for k, ok in enumerate(flags, start=1) if ok), default=0)
    return pd.Series([out[p] for p in D.index],
                     index=D.index.copy(), name="L")


# ── 지표 조립 ────────────────────────────────────────────────────────────────
def assemble_metrics(places: pd.DataFrame, D: pd.Series, A: pd.Series, L: pd.Series,
                     lstar_gate_min_degree: int) -> pd.DataFrame:
    """모든 place에 대해 지표를 1:1로 만든다(C-S03-B-001). L* = L if D >= gate else 0."""
    ids = places["place_id"].astype(str)
    gate = int(lstar_gate_min_degree)
    m = pd.DataFrame({
        "place_id": ids.to_numpy(),
        "name_norm": places["name_norm"].astype(str).to_numpy(),
        "D": D.loc[ids].to_numpy(),
        "A": A.loc[ids].to_numpy(),
        "L": L.loc[ids].to_numpy(),
    })
    m["L_star"] = np.where(m["D"].astype(int) >= gate, m["L"].astype(int), 0)
    m["in_lspace"] = m["D"].astype(int) >= 1
    for c in ("D", "A", "L", "L_star"):
        m[c] = m[c].astype("int16")
    m["in_lspace"] = m["in_lspace"].astype(bool)
    assert len(m) == len(places), "len(metrics) != len(places) — 전량 검증 규칙 위반"
    return (m[METRIC_COLUMNS]
            .sort_values("place_id", kind="mergesort").reset_index(drop=True))


# ── sensitivity (spec §5.4 — 정식 산출물) ───────────────────────────────────
def _variant_id(mask: str, dom: str, k: int, theta: float) -> str:
    return f"{mask}|{dom}|k{int(k)}|theta{float(theta):g}"


def _qualified_counts(place_ids, D, A, L, gate: int, thresholds: dict) -> tuple[int, int]:
    lstar = np.where(D.astype(int) >= gate, L.astype(int), 0)
    q = qualify_mod.qualify(pd.DataFrame({
        "place_id": place_ids, "D": D.to_numpy(), "A": A.to_numpy(), "L_star": lstar}),
        thresholds)
    return (int((q["hub_class_rule"] == "CROSSING").sum()),
            int((q["hub_class_rule"] == "TERMINAL").sum()))


def build_sensitivity(places: pd.DataFrame, edges: pd.DataFrame, D: pd.Series,
                      tables: dict, params: dict,
                      arms_by_theta: dict | None = None) -> pd.DataFrame:
    """민감도 격자 = lifetime 3축(mask×dominance×k_max, θ=채택값) + arm θ 변형(채택 lifetime).

    is_adopted=True 행은 정확히 1개(params 채택 조합) — C-S03-B-007이 재계산 대조한다.
    override는 미적용(순수 함수 감도만).
    """
    grid = params["sensitivity_grid"]
    adopted_theta = float(params["arm_theta_deg"])
    lt = params["lifetime"]
    adopted = (str(lt["mask_mode"]), str(lt["dominance"]), int(lt["k_max"]), adopted_theta)
    gate = int(params["lstar_gate_min_degree"])
    thresholds = params["thresholds"]
    ids = places["place_id"].astype(str)

    arms_by_theta = dict(arms_by_theta or {})
    thetas = sorted({adopted_theta} | {float(t) for t in grid["arm_theta_deg"]})
    for t in thetas:
        if t not in arms_by_theta:
            arms_by_theta[t] = compute_arms(edges, places, t)

    rows = []
    for mask in grid["mask_mode"]:
        for dom in grid["dominance"]:
            for k in grid["k_max"]:
                L = compute_lifetime(edges, places, D, str(mask), str(dom), int(k),
                                     tables=tables)
                nc, nt = _qualified_counts(ids, D.loc[ids], arms_by_theta[adopted_theta].loc[ids],
                                           L.loc[ids], gate, thresholds)
                rows.append((_variant_id(mask, dom, k, adopted_theta),
                             str(mask), str(dom), int(k), adopted_theta,
                             nc, nt, nc + nt,
                             (str(mask), str(dom), int(k), adopted_theta) == adopted))
    L_adopted = compute_lifetime(edges, places, D, *adopted[:3], tables=tables)
    for t in (float(t) for t in grid["arm_theta_deg"]):
        if t == adopted_theta:
            continue                        # 채택 θ 행은 lifetime 격자에 이미 존재
        nc, nt = _qualified_counts(ids, D.loc[ids], arms_by_theta[t].loc[ids],
                                   L_adopted.loc[ids], gate, thresholds)
        rows.append((_variant_id(*adopted[:3], t), adopted[0], adopted[1], adopted[2], t,
                     nc, nt, nc + nt, False))
    return pd.DataFrame(rows, columns=SENSITIVITY_COLUMNS)


# ── D=0 사유 설명 (ADR-010 / spec §5.5) ─────────────────────────────────────
def annotate_gap(places: pd.DataFrame, metrics: pd.DataFrame,
                 s01_patterns: pd.DataFrame, s01_pattern_stops: pd.DataFrame,
                 stop2place: pd.Series, route_catalog: pd.DataFrame,
                 universe: str) -> pd.DataFrame:
    """diag_lspace_gap.csv — D==0 place 전량의 사유 분해 (그래프 구축에는 사용하지 않음).

    사유별 합 == D==0 place 수 (C-S03-B-010 accounting).
    """
    uroutes = universe_route_set(route_catalog, universe)
    pmeta = s01_patterns.set_index("pattern_id")[["route", "role", "in_backbone"]]

    ps = s01_pattern_stops[["pattern_id", "stop_id"]].copy()
    ps["place_id"] = ps["stop_id"].map(stop2place)
    pat_of_place: dict[str, set] = {}
    for pid, g in ps.dropna(subset=["place_id"]).groupby("place_id"):
        pat_of_place[str(pid)] = set(g["pattern_id"])

    name_of = places.set_index("place_id")["name_norm"]
    d0 = metrics.loc[metrics["D"].astype(int) == 0, "place_id"].astype(str)
    rows = []
    for pid in sorted(d0):
        pats = sorted(pat_of_place.get(pid, ()))
        if not pats:
            rows.append((pid, str(name_of.get(pid, "")), "no_pattern", ""))
            continue
        causes, routes = set(), set()
        for pat in pats:
            route = str(pmeta.at[pat, "route"])
            routes.add(route)
            if route not in uroutes:
                causes.add("out_of_universe")
            elif bool(pmeta.at[pat, "in_backbone"]):
                causes.add("backbone_visible")   # D=0인데 backbone 서비스 — 단일 place 붕괴만 가능
            elif str(pmeta.at[pat, "role"]) == "circular":
                causes.add("uncovered_circular") # circular-with-base (ADR-006 미포함 계열)
            else:
                causes.add("non_backbone_variant")
        reason = (f"{next(iter(causes))}_only" if len(causes) == 1
                  else "mixed_nonbackbone")
        rows.append((pid, str(name_of.get(pid, "")), reason, "|".join(sorted(routes))))
    return pd.DataFrame(rows, columns=GAP_COLUMNS)


# ── _semantics.yaml (design.md §8의 s03 적용 — ADR-010 결정 2) ──────────────
_SEMANTICS_TEXT = """\
# s03_hub / {scope} — 독자(reader-side) 표현 가이드 (design.md §8, ADR-010)
layer: lspace_hub
stage: s03_hub
scope: {scope}
universe: {universe}
allowed_statements:
  - "이 place는 General L-space에서 D=k다"
  - "이 place는 채택 임계에서 CROSSING/TERMINAL/NONE으로 판정되었다 (params 상대적)"
  - "D=0은 이 universe(backbone × General)의 L-space가 그 place를 볼 수 없다는 증거 부재다"
forbidden_statements:
  - "D=0인 place는 환승 가치가 없다"
  - "NONE = 환승 불가"
  - "hub_class는 universe·params와 무관한 절대 판정이다"
notes:
  - "hub_class는 params·universe 상대적 판정이다 — 교정 수단은 hub_overrides(사용자 판단)"
  - "미커버 노선의 변형(circular-with-base)은 배제가 아니라 보존이며 시간층(s05)의 몫이다"
"""


def write_semantics(vdir: Path, scope: str, universe: str) -> Path:
    p = Path(vdir) / "_semantics.yaml"
    paths.assert_writable(p)
    p.write_text(_SEMANTICS_TEXT.format(scope=scope, universe=universe), encoding="utf-8")
    return p


# ── 빌드 엔트리 (러너 규약: build(inputs, params, vdir)) ─────────────────────
def _write_parquet(df: pd.DataFrame, path: Path) -> Path:
    paths.assert_writable(path)
    df.to_parquet(path, index=False)
    return path


def _write_csv(df: pd.DataFrame, path: Path) -> Path:
    paths.assert_writable(path)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def build_before(inputs, params: dict, vdir: Path) -> dict[str, Path]:
    scope = "before"
    vdir = Path(vdir)
    s01 = paths.artifact_dir("s01_canonical", scope,
                             inputs.artifacts[f"s01_canonical/{scope}"]["version"])
    s02 = paths.artifact_dir("s02_place", scope,
                             inputs.artifacts[f"s02_place/{scope}"]["version"])
    backbone = pd.read_parquet(s01 / "backbone_stops.parquet")
    route_catalog = pd.read_parquet(s01 / "route_catalog.parquet")
    patterns = pd.read_parquet(s01 / "patterns.parquet")            # D=0 사유 설명용
    pattern_stops = pd.read_parquet(s01 / "pattern_stops.parquet")  # D=0 사유 설명용
    places = pd.read_parquet(s02 / "places.parquet")
    stop_map = pd.read_parquet(s02 / "stop_place_map.parquet")
    stop2place = stop_map.set_index("stop_id")["place_id"]

    universe = str(params["universe"])
    uroutes = universe_route_set(route_catalog, universe)
    edges = build_lspace(backbone, stop2place, uroutes, places)

    D = compute_degree(edges, places)
    theta = float(params["arm_theta_deg"])
    A = compute_arms(edges, places, theta)
    lt = params["lifetime"]
    grid = params["sensitivity_grid"]
    k_cap = max([int(k) for k in grid["k_max"]] + [int(lt["k_max"])])
    tables = dominance_tables(_adjacency(edges), D, k_cap)
    L = compute_lifetime(edges, places, D, str(lt["mask_mode"]), str(lt["dominance"]),
                         int(lt["k_max"]), tables=tables)
    metrics = assemble_metrics(places, D, A, L, int(params["lstar_gate_min_degree"]))

    rule = qualify_mod.qualify(metrics, params["thresholds"])
    overrides = qualify_mod.load_hub_overrides()
    qualification = qualify_mod.apply_hub_overrides(rule, overrides)

    sensitivity = build_sensitivity(places, edges, D, tables, params,
                                    arms_by_theta={theta: A})
    gap = annotate_gap(places, metrics, patterns, pattern_stops, stop2place,
                       route_catalog, universe)

    out = {
        "place_metrics.parquet": _write_parquet(metrics, vdir / "place_metrics.parquet"),
        "hub_qualification.parquet": _write_parquet(qualification,
                                                    vdir / "hub_qualification.parquet"),
        "l_space_place_edges.parquet": _write_parquet(edges,
                                                      vdir / "l_space_place_edges.parquet"),
        "sensitivity.csv": _write_csv(sensitivity, vdir / "sensitivity.csv"),
        "diag_lspace_gap.csv": _write_csv(gap, vdir / "diag_lspace_gap.csv"),
        "_semantics.yaml": write_semantics(vdir, scope, universe),
    }

    d_int = metrics["D"].astype(int)
    meta = {
        "scope": scope, "universe": universe,
        "n_universe_routes": len(uroutes),
        "n_lspace_routes": int(pd.Series(
            [r for rs in edges["routes"] for r in rs.split("|")]).nunique()) if len(edges) else 0,
        "n_places": int(len(places)), "n_edges": int(len(edges)),
        "degree_dist": {"0": int((d_int == 0).sum()), "1": int((d_int == 1).sum()),
                        "2": int((d_int == 2).sum()), "3+": int((d_int >= 3).sum())},
        "n_crossing": int((qualification["hub_class"] == "CROSSING").sum()),
        "n_terminal": int((qualification["hub_class"] == "TERMINAL").sum()),
        "n_hub_qualified": int(qualification["hub_class"].isin(["CROSSING", "TERMINAL"]).sum()),
        "n_overrides_applied": int(len(overrides)),
        "sensitivity_hub_range": [int(sensitivity["n_hub_qualified"].min()),
                                  int(sensitivity["n_hub_qualified"].max())] if len(sensitivity) else None,
    }
    meta_p = vdir / "hub_meta.json"
    paths.assert_writable(meta_p)
    with open(meta_p, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    out["hub_meta.json"] = meta_p
    return out
