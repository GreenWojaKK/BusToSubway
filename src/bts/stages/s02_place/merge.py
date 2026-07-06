"""s02_place — 정류장을 장소(place)로 묶기 (design.md §5 s02, stage2_place_hub_spec.md §4).

핵심 규칙: **"이름은 blocking key, 병합 key가 아님."**
① normalize(v1 strip) + name_aliases 치환(정규화 후)
② 같은 name_norm 블록 안에서만 single-linkage(union-find, ≤ linkage_max_m)
③ pre-override place = (name_norm, cluster) — place_id는 콘텐츠 파생 결정적 id
④ place_overrides.{scope}.csv 적용(action=merge만, 파일 행 순서) + override_applied.csv
⑤ 진단(diagnostics.py — 제안일 뿐, 병합 아님)

구조적 불변식:
- 다른 name_norm 간 union 코드 경로가 존재하지 않는다(cross-name 병합의 구조적 차단
  — C-S02-*-002의 성립 근거). 예외는 override merge 행뿐이며 is_override_merged로 표기된다.
- place는 stop을 대체하지 않는다 — stop_place_map은 모든 stop을 빠짐없이 매핑한다(C-S02-*-001).
- 임계값은 전부 params(stages.s02_place) — 코드 내 수치 임계 리터럴 없음.
- override 행이 실린 빌드의 검토 승인(--reviewed-by)은 러너 소관(registry
  review_overrides)이며, 스테이지 코드는 재구현하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import bts.paths as paths
from bts.io import ContractViolation, normalize
from bts.stages.s02_place import diagnostics

_SCOPE_PREFIX = {"before": "PB", "after": "PA"}

OVERRIDE_COLUMNS = ["action", "place_a", "place_b", "reason", "source"]
OVERRIDE_ACTIONS = ("merge",)   # split 등 확장은 별도 ADR (spec §8-4 open question)
APPLIED_COLUMNS = ["row_no", "action", "place_a", "place_b", "result_place_id",
                   "n_stops_merged", "reason", "source"]
PLACE_COLUMNS = ["place_id", "place_name", "name_norm", "n_stops", "n_names",
                 "lat_centroid", "lon_centroid", "span_m", "is_override_merged"]
MAP_COLUMNS = ["stop_id", "place_id", "name_norm", "dist_to_centroid_m"]

_CENTROID_ROUND = 6   # 좌표 표기 자릿수 규약 (s00 stops round(6)와 동일 — 임계값 아님)


# ── place_id ─────────────────────────────────────────────────────────────────
def make_place_id(scope: str, name_norm: str, member_stop_ids) -> str:
    """P{B|A}_ + sha1((name_norm + '|' + min(member_stop_ids)))[:8] — 콘텐츠 파생.

    멤버 순서와 무관하며 재실행해도 동일하다(design.md §5 s02). 충돌은 C-S02-*-008이 검출한다.
    """
    members = [str(m) for m in member_stop_ids]
    if not members:
        raise ContractViolation("make_place_id: 멤버 없는 place는 존재할 수 없다")
    payload = (str(name_norm) + "|" + min(members)).encode("utf-8")
    return _SCOPE_PREFIX[scope] + "_" + hashlib.sha1(payload).hexdigest()[:8]


# ── ①② 정규화 + blocking single-linkage ─────────────────────────────────────
def _normalized_names(stops: pd.DataFrame, aliases: dict, meta: dict | None) -> pd.Series:
    """normalize_stop_name(v1 strip) 후 alias 치환. 적용 건수는 meta에 기록."""
    names = stops["stop_name"].astype(str).map(normalize.normalize_stop_name)
    hit = names.isin(aliases.keys())
    if meta is not None:
        meta["n_alias_applied"] = int(hit.sum())
    return names.where(~hit, names.map(aliases))


def _uf_find(parent: list, i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def cluster_same_name(stops: pd.DataFrame, linkage_max_m: float,
                      aliases: dict | None = None, meta: dict | None = None) -> pd.Series:
    """stop_id → (name_norm, 클러스터 대표=최소 stop_id) 라벨 Series.

    ① name_norm = normalize_stop_name(stop_name) (v1 strip) + name_aliases 치환(정규화 후)
    ② 같은 name_norm 블록 안에서만 union-find single-linkage:
       haversine_m ≤ linkage_max_m 이면 union (경계 포함 ≤ — 단위 테스트 고정).
    ③ 다른 name_norm 간 union 경로는 코드 상 존재하지 않는다.
    재현성: 블록·stop 순회는 정렬 순서로 고정한다.
    stops 요구 컬럼: stop_id, stop_name, lat, lon.
    """
    aliases = {} if aliases is None else dict(aliases)
    stop_rows = stops[["stop_id", "stop_name", "lat", "lon"]].copy()
    stop_rows["name_norm"] = _normalized_names(stop_rows, aliases, meta)
    stop_rows = stop_rows.sort_values("stop_id", kind="mergesort").reset_index(drop=True)

    idx, labels = [], []
    for name, block in stop_rows.groupby("name_norm", sort=True):
        ids = block["stop_id"].tolist()          # stop_id 오름차순 (위 정렬 유지)
        n = len(ids)
        parent = list(range(n))
        if n > 1:
            la = block["lat"].to_numpy(float)
            lo = block["lon"].to_numpy(float)
            d = np.atleast_2d(normalize.haversine_m(
                la[:, None], lo[:, None], la[None, :], lo[None, :]))
            close = np.triu(d <= float(linkage_max_m), k=1)
            for i, j in np.argwhere(close):
                ri, rj = _uf_find(parent, int(i)), _uf_find(parent, int(j))
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
        rep: dict[int, str] = {}
        for i in range(n):                        # 오름차순 → 대표 = 컴포넌트 최소 stop_id
            r = _uf_find(parent, i)
            rep.setdefault(r, ids[i])
        for i in range(n):
            idx.append(ids[i])
            labels.append((name, rep[_uf_find(parent, i)]))
    return pd.Series(labels, index=pd.Index(idx, name="stop_id"), name="cluster")


# ── ④ override ──────────────────────────────────────────────────────────────
def load_overrides(scope: str, path: Path | None = None) -> pd.DataFrame:
    """config/overrides/place_overrides.{scope}.csv — 헤더만 있는 빈 파일도 허용한다."""
    p = Path(path) if path is not None else (
        paths.CONFIG / "overrides" / f"place_overrides.{scope}.csv")
    df = pd.read_csv(p, encoding="utf-8-sig", dtype=str)
    if list(df.columns) != OVERRIDE_COLUMNS:
        raise ContractViolation(
            f"place_overrides.{scope}.csv 헤더 위반: {list(df.columns)} != {OVERRIDE_COLUMNS}")
    return df


def apply_overrides(places_pre: pd.DataFrame, map_pre: pd.DataFrame,
                    overrides: pd.DataFrame, scope: str
                    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """수동 병합 목록을 적용한다(spec §4.2). 반환: (places 골격, stop_place_map 골격, override_applied).

    - action enum: {merge}. 그 외 → ContractViolation.
    - 적용 순서 = 파일 행 순서(유일한 결정론 축). 각 행은 '그 행 적용 시점'의 place_id를
      참조한다 — 연쇄 병합이면 앞 행이 만든 새 place_id를 뒤 행이 참조해야 한다.
    - merge: place_b의 stop 전량을 place_a에 흡수. 대표 이름 = place_a의 name_norm.
      place_id는 make_place_id(scope, name_norm(place_a), union)로 재계산.
    - 참조 불능(존재하지 않는 place_id) → ContractViolation.
    - override_applied.n_stops_merged = 병합 결과 place의 stop 총수(union 크기).
    골격 = [place_id, name_norm, is_override_merged] / [stop_id, place_id, name_norm].
    기하·집계 컬럼은 assemble_places가 재계산한다.
    """
    member_of = map_pre.groupby("place_id")["stop_id"].agg(list)
    state = {r.place_id: {"name_norm": r.name_norm,
                          "members": sorted(member_of.get(r.place_id, [])),
                          "merged": bool(r.is_override_merged)}
             for r in places_pre.itertuples()}

    applied = []
    for row_no, row in enumerate(overrides.itertuples(index=False), start=1):
        action = str(row.action)
        if action not in OVERRIDE_ACTIONS:
            raise ContractViolation(
                f"place_overrides.{scope} {row_no}행: 미지 action '{action}' "
                f"(허용: {list(OVERRIDE_ACTIONS)})")
        a, b = str(row.place_a), str(row.place_b)
        if a == b:
            raise ContractViolation(
                f"place_overrides.{scope} {row_no}행: place_a == place_b ({a}) — 자기 병합 불가")
        for ref in (a, b):
            if ref not in state:
                raise ContractViolation(
                    f"place_overrides.{scope} {row_no}행: 참조 불능 place_id '{ref}' — "
                    f"각 행은 그 행 적용 시점의 place_id를 참조한다(연쇄 병합은 앞 행 결과 id)")
        sa, sb = state.pop(a), state.pop(b)
        name = sa["name_norm"]
        members = sorted(sa["members"] + sb["members"])
        new_id = make_place_id(scope, name, members)
        state[new_id] = {"name_norm": name, "members": members, "merged": True}
        applied.append({"row_no": row_no, "action": action, "place_a": a, "place_b": b,
                        "result_place_id": new_id, "n_stops_merged": len(members),
                        "reason": row.reason, "source": row.source})

    stop_name = map_pre.set_index("stop_id")["name_norm"]
    map_rows = [(m, pid) for pid in sorted(state) for m in state[pid]["members"]]
    new_map = pd.DataFrame(map_rows, columns=["stop_id", "place_id"])
    new_map["name_norm"] = new_map["stop_id"].map(stop_name)
    new_map = new_map.sort_values("stop_id", kind="mergesort").reset_index(drop=True)
    skel = pd.DataFrame(
        [(pid, state[pid]["name_norm"], state[pid]["merged"]) for pid in sorted(state)],
        columns=["place_id", "name_norm", "is_override_merged"])
    applied_df = pd.DataFrame(applied, columns=APPLIED_COLUMNS)
    return skel, new_map, applied_df


# ── 기하·집계 재계산 ─────────────────────────────────────────────────────────
def _max_pairwise_m(lat: np.ndarray, lon: np.ndarray) -> float:
    """멤버 stop 최대 쌍거리 (n==1 → 0.0)."""
    if len(lat) < 2:
        return 0.0
    d = normalize.haversine_m(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    return float(np.max(d))


def assemble_places(places_skel: pd.DataFrame, map_df: pd.DataFrame,
                    stops: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """골격 + 좌표 → places.parquet / stop_place_map.parquet 스키마(§4.4) 조립.

    n_stops·n_names·centroid(round6)·span_m·dist_to_centroid_m 전부 멤버에서 재계산 —
    C-S02-*-008의 재계산 대조가 이 함수와 같은 정의를 공유한다.
    """
    mp = map_df[["stop_id", "place_id", "name_norm"]].merge(
        stops[["stop_id", "lat", "lon"]], on="stop_id", how="left", validate="one_to_one")
    if mp[["lat", "lon"]].isna().any().any():
        raise ContractViolation("stop_place_map에 stops 밖 stop_id 존재 — 좌표 해소 실패")

    rep_name = places_skel.set_index("place_id")["name_norm"]
    rep_merged = places_skel.set_index("place_id")["is_override_merged"]
    rows, dist_of = [], {}
    for pid, g in mp.groupby("place_id", sort=True):
        la, lo = g["lat"].to_numpy(float), g["lon"].to_numpy(float)
        lat_c = round(float(la.mean()), _CENTROID_ROUND)
        lon_c = round(float(lo.mean()), _CENTROID_ROUND)
        rows.append((pid, str(rep_name.loc[pid]), int(len(g)), int(g["name_norm"].nunique()),
                     lat_c, lon_c, _max_pairwise_m(la, lo), bool(rep_merged.loc[pid])))
        d = np.atleast_1d(normalize.haversine_m(la, lo, lat_c, lon_c))
        for stop, dv in zip(g["stop_id"], d):
            dist_of[stop] = float(dv)

    places = pd.DataFrame(rows, columns=["place_id", "name_norm", "n_stops", "n_names",
                                         "lat_centroid", "lon_centroid", "span_m",
                                         "is_override_merged"])
    places["place_name"] = places["name_norm"]          # 대표 name_norm (§4.4)
    places["n_stops"] = places["n_stops"].astype("int16")
    places["n_names"] = places["n_names"].astype("int16")
    places["span_m"] = places["span_m"].astype("float64")
    places = (places[PLACE_COLUMNS]
              .sort_values("place_id", kind="mergesort").reset_index(drop=True))

    out_map = map_df[["stop_id", "place_id", "name_norm"]].copy()
    out_map["dist_to_centroid_m"] = out_map["stop_id"].map(dist_of).astype("float64")
    out_map = (out_map[MAP_COLUMNS]
               .sort_values("stop_id", kind="mergesort").reset_index(drop=True))
    return places, out_map


# ── 순수 조립 (파일 IO 없음 — _build와 테스트가 공유) ────────────────────────
def build_frames(stops: pd.DataFrame, params: dict, scope: str,
                 overrides: pd.DataFrame, aliases: dict | None = None,
                 meta: dict | None = None) -> dict[str, pd.DataFrame]:
    """①~⑤ 전 절차의 순수 함수 형태. 반환: 산출물 이름 → DataFrame."""
    meta = meta if meta is not None else {}
    labels = cluster_same_name(stops, float(params["linkage_max_m"]), aliases, meta)

    members: dict[tuple, list] = {}
    for pid, lab in labels.items():
        members.setdefault(lab, []).append(pid)
    skel_rows, map_rows = [], []
    for (name, _), mem in sorted(members.items()):
        place_id = make_place_id(scope, name, mem)
        skel_rows.append((place_id, name, False))
        map_rows.extend((m, place_id, name) for m in mem)
    places_pre = pd.DataFrame(skel_rows, columns=["place_id", "name_norm", "is_override_merged"])
    map_pre = (pd.DataFrame(map_rows, columns=["stop_id", "place_id", "name_norm"])
               .sort_values("stop_id", kind="mergesort").reset_index(drop=True))
    meta["n_places_pre_override"] = int(len(places_pre))

    skel, map_skel, applied = apply_overrides(places_pre, map_pre, overrides, scope)
    meta["n_overrides_applied"] = int(len(applied))

    places, stop_place_map = assemble_places(skel, map_skel, stops)
    meta["n_places"] = int(len(places))
    meta["name_nunique"] = int(stop_place_map["name_norm"].nunique())

    return {
        "places": places,
        "stop_place_map": stop_place_map,
        "override_applied": applied,
        "diag_under_merge": diagnostics.diag_under_merge(places, stop_place_map, stops, params),
        "diag_alias": diagnostics.diag_alias(places, stop_place_map, stops, params),
    }


# ── 빌드 엔트리 (러너 규약: build(inputs, params, vdir)) ─────────────────────
def _write_parquet(df: pd.DataFrame, path: Path) -> Path:
    paths.assert_writable(path)
    df.to_parquet(path, index=False)
    return path


def _write_csv(df: pd.DataFrame, path: Path) -> Path:
    paths.assert_writable(path)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _build(scope: str, inputs, params: dict, vdir: Path) -> dict[str, Path]:
    """stops(s00 artifact) → build_frames → 산출 5종 + place_meta.json."""
    vdir = Path(vdir)
    up = inputs.artifacts[f"s00_ingest/{scope}"]
    stops = pd.read_parquet(
        paths.artifact_dir("s00_ingest", scope, up["version"]) / "stops.parquet")

    meta: dict = {"scope": scope, "n_stops": int(len(stops))}
    frames = build_frames(stops, params, scope,
                          overrides=load_overrides(scope),
                          aliases=normalize.load_aliases(), meta=meta)

    out = {
        "places.parquet": _write_parquet(frames["places"], vdir / "places.parquet"),
        "stop_place_map.parquet": _write_parquet(frames["stop_place_map"],
                                                 vdir / "stop_place_map.parquet"),
        "diag_under_merge.csv": _write_csv(frames["diag_under_merge"],
                                           vdir / "diag_under_merge.csv"),
        "diag_alias.csv": _write_csv(frames["diag_alias"], vdir / "diag_alias.csv"),
        "override_applied.csv": _write_csv(frames["override_applied"],
                                           vdir / "override_applied.csv"),
    }
    meta_p = vdir / "place_meta.json"
    paths.assert_writable(meta_p)
    with open(meta_p, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    out["place_meta.json"] = meta_p
    return out


def build_before(inputs, params, vdir) -> dict[str, Path]:
    return _build("before", inputs, params, vdir)


def build_after(inputs, params, vdir) -> dict[str, Path]:
    return _build("after", inputs, params, vdir)
