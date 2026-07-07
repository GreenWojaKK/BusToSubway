# s01_canonical/before 계약 체크 테스트 — 실패해야 할 조건을 합성 데이터로 주입한다.
# ★ 핵심 fixture: "전 role self→NaN 정규화를 흉내낸 tags"(circular 자기참조 34 소거)에서
#   C-S01-B-004가 실제로 FAIL(413≠379 검출)해야 한다.
import pandas as pd
import pytest

import bts.paths as paths
from bts.checks.contracts import s01_before as chk
from bts.stages.s01_canonical import before as s01b


def _params():
    return paths.load_params()["stages"]["s01_canonical"]


def make_tagged(erase_circular_self=False):
    """감사 실측 role·base 분포를 재현한 합성 판정표 (481행).

    erase_circular_self=True: '전 role base==self→NaN' 흉내 — circular 자기참조 34 소거.
    """
    rows = []

    def add(pid, role, base):
        rows.append({"pattern_id": pid, "role": role, "base_pattern_id_raw": base})

    mains = [f"M{i:03d}" for i in range(187)]
    for i, pid in enumerate(mains):
        add(pid, "main", pid if i < 92 else None)          # 자기참조 92 [VT§3]
    for i in range(103):                                    # circular 66 base 없는+34 자기+3 타행
        pid = f"C{i:03d}"
        if i < 66:
            base = None
        elif i < 100:
            base = None if erase_circular_self else pid
        else:
            base = mains[0]
        add(pid, "circular", base)
    sts = [f"S{i:03d}" for i in range(112)]
    add(sts[0], "short_turn", mains[0])
    add(sts[1], "short_turn", sts[0])                       # 체인 (147형) — 재해소 1
    for pid in sts[2:]:
        add(pid, "short_turn", mains[1])
    for i in range(14):
        add(f"B{i:03d}", "branch", mains[2])
    for i in range(51):                                     # detour→branch 2, →short_turn 1
        base = "B000" if i < 2 else (sts[2] if i == 2 else mains[3])
        add(f"D{i:03d}", "detour", base)
    for i in range(10):
        add(f"E{i:03d}", "extension", mains[4])
    for i in range(2):
        add(f"U{i:03d}", "duplicate", mains[5])
    for i in range(2):
        add(f"A{i:03d}", "anomaly", None)
    return pd.DataFrame(rows)


def make_patterns(erase_circular_self=False):
    """합성 tags + 지원 6 → 실제 빌드 함수(resolve→disposition)로 patterns 프레임 생성."""
    df = make_tagged(erase_circular_self)
    sup = pd.DataFrame({"pattern_id": chk._SUPPORT_IDS,
                        "role": "support", "base_pattern_id_raw": None})
    df = pd.concat([df, sup], ignore_index=True)
    p = _params()
    df = s01b.resolve_base_refs(df, max_depth=p["base_ref_max_depth"])
    df = s01b.build_disposition(df, p["canonical_roles"], p["backbone_roles"])
    # c011용 is_loop 실측 분포: circular 중 3만 False, main 중 11만 True [VT§2]
    df["is_loop"] = False
    circ = df.index[df["role"] == "circular"]
    df.loc[circ, "is_loop"] = True
    df.loc[circ[:3], "is_loop"] = False
    df.loc[df.index[df["role"] == "main"][:11], "is_loop"] = True
    return df


class TestC004CanonicalFormula:
    def test_정상_379_PASS(self):
        pat = make_patterns()
        canon = pat[pat["in_canonical"]]
        r = chk.c004_canonical_formula(pat, canon, _params()["canonical_roles"])
        assert r.status == "PASS"
        assert r.observed["total"] == 379

    def test_전_role_정규화_흉내는_413_검출_FAIL(self):
        # ★ circular 자기참조 34 소거 → base 없는 circular 100 → canonical 413 ≠ 379
        pat = make_patterns(erase_circular_self=True)
        canon = pat[pat["in_canonical"]]
        r = chk.c004_canonical_formula(pat, canon, _params()["canonical_roles"])
        assert r.status == "FAIL"
        assert r.observed["total"] == 413
        assert r.observed["parts"]["circular_baseless"] == 100


class TestC003BaseScope:
    def test_정상_92_34_4_PASS(self):
        r = chk.c003_base_scope(make_patterns())
        assert r.status == "PASS"
        assert r.observed["rechained"] == 4

    def test_circular_자기참조_소거는_FAIL(self):
        r = chk.c003_base_scope(make_patterns(erase_circular_self=True))
        assert r.status == "FAIL"
        assert r.observed["circular_selfref_preserved"] == 0


class TestC005DispositionAccounting:
    def test_정상_487_전수_PASS(self):
        r = chk.c005_disposition_accounting(make_patterns())
        assert r.status == "PASS"
        assert r.observed["sum"] == 487

    def test_detour_1행_누락은_FAIL(self):
        disp = make_patterns()
        drop = disp.index[disp["disposition"] == "excl_detour"][:1]
        r = chk.c005_disposition_accounting(disp.drop(index=drop))
        assert r.status == "FAIL"
        assert r.observed["sum"] == 486


class TestC002JoinDirection:
    @staticmethod
    def _ta(n_tagged=7568, n_support=57):
        return pd.DataFrame({
            "trip_id": [f"T{i}" for i in range(n_tagged + n_support)],
            "attribution": ["tagged"] * n_tagged + ["support"] * n_support})

    def test_지원_6패턴_exact_PASS(self):
        pat = make_patterns()
        vt = make_tagged()
        r = chk.c002_join_direction(pat, vt, self._ta())
        assert r.status == "PASS"

    def test_지원_목록_이탈은_FAIL(self):
        pat = make_patterns()
        pat.loc[pat["pattern_id"] == chk._SUPPORT_IDS[0], "pattern_id"] = "BR_TAGO_USB999999999"
        r = chk.c002_join_direction(pat, make_tagged(), self._ta())
        assert r.status == "FAIL"


class TestC009Duplicates:
    @staticmethod
    def _vt():
        return pd.DataFrame({"pattern_id": ["X1", "X2"], "role": ["duplicate"] * 2,
                             "route": ["323", "323"]})

    def test_정확히_4건_PASS(self):
        dup = pd.DataFrame({"routes": ["22|977", "22|977", "941|948", "527|537|808"],
                            "pattern_key": list("abcd")})
        r = chk.c009_duplicates(dup, self._vt())
        assert r.status == "PASS"

    def test_1건_소실은_FAIL(self):
        dup = pd.DataFrame({"routes": ["22|977", "941|948", "527|537|808"],
                            "pattern_key": list("abc")})
        r = chk.c009_duplicates(dup, self._vt())
        assert r.status == "FAIL"


class TestC011IsLoopPC:
    def test_실측_예외_3_11_실재_PASS(self):
        r = chk.c011_is_loop_pc(make_patterns())
        assert r.status == "PASS" and r.positive_control

    def test_예외_소거는_FAIL(self):
        # 양성 대조군의 자기 검증 — 이상치가 사라져도(0건) FAIL이어야 한다
        pat = make_patterns()
        pat["is_loop"] = pat["role"] == "circular"
        r = chk.c011_is_loop_pc(pat)
        assert r.status == "FAIL"


class TestP002BackboneCoverage:
    """ADR-006: 커버리지는 실측 계약(고정값+목록)으로 '변화'를 감시한다."""

    @staticmethod
    def _frames():
        pat = make_patterns()
        # 합성 route: main 보유 R0(커버) + circular_with_base만 보유 RC66..(미커버)
        pat["route"] = "R0"
        cwb = pat["role_scope"] == "circular_with_base"
        pat.loc[cwb, "route"] = ["RC%02d" % i for i in range(int(cwb.sum()))]
        bb = pd.DataFrame({"pattern_id": pat.loc[pat["in_backbone"], "pattern_id"]})
        return pat, bb

    def test_고정값_일치_PASS(self):
        pat, bb = self._frames()
        uncovered = sorted(pat.loc[pat["role_scope"] == "circular_with_base", "route"])
        r = chk.p002_backbone_coverage(pat, bb, exp_covered=1, exp_uncovered=uncovered,
                                       exp_total=1 + len(uncovered))
        assert r.status == "PASS"

    def test_커버리지_변화는_FAIL(self):
        pat, bb = self._frames()
        uncovered = sorted(pat.loc[pat["role_scope"] == "circular_with_base", "route"])
        # 어느 방향의 변화든 검출: 미커버 1건이 '해소'된 상황을 주입
        r = chk.p002_backbone_coverage(pat, bb, exp_covered=1,
                                       exp_uncovered=uncovered[:-1],
                                       exp_total=1 + len(uncovered))
        assert r.status == "FAIL"


class TestC003CanonicalAncestor:
    def test_circular_with_base_종착은_noncanonical로_검출(self):
        # spec §5.1: 종착 조상은 canonical(main/base 없는 circular)이어야 한다 —
        # 조건이 role in {main, circular} 정도로 넓으면 이 위반을 놓친다.
        pat = make_patterns()
        cwb = pat.loc[pat["role_scope"] == "circular_with_base", "pattern_id"].iloc[0]
        idx = pat.index[pat["role"] == "detour"][:1]
        pat.loc[idx, "base_ref_resolved"] = cwb              # 위반 주입
        r = chk.c003_base_scope(pat)
        assert r.status == "FAIL"
        assert r.observed["derived_resolved_noncanonical"] >= 1


class TestC007RouteCoverage:
    def test_main_circular_없는_route_검출(self):
        pat = make_patterns()
        pat["route"] = "R1"                      # 전 패턴 한 노선 — main 존재 → PASS
        pat["direction_group"] = pat["pattern_id"]   # dg당 main 1 유지
        r = chk.c007_route_coverage(pat)
        assert r.observed["routes_without_main_or_circular"] == 0
        only_st = pat[pat["role"].isin(["short_turn", "support"])].copy()
        only_st["route"] = only_st["role"].map({"short_turn": "R2", "support": "R3"})
        r2 = chk.c007_route_coverage(pd.concat([pat, only_st], ignore_index=True))
        assert r2.status == "FAIL"               # R2에는 main/circular 없음
