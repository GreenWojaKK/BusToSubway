# s03 lifetime(L) 단위 테스트 — ADR-008 masked/subgraph × weak/strict × K
# (stage2_place_hub_spec.md §6.1)
import pandas as pd
import pytest

from bts.io import ContractViolation
from bts.stages.s03_hub import metrics as m

_K = 10


def _graph(edge_pairs):
    edges = pd.DataFrame([tuple(sorted(e)) for e in edge_pairs],
                         columns=["place_a", "place_b"])
    ids = sorted({p for e in edge_pairs for p in e})
    places = pd.DataFrame({"place_id": ids, "name_norm": ids,
                           "lat_centroid": [35.5] * len(ids),
                           "lon_centroid": [129.3] * len(ids)})
    return edges, places


def _lifetime(edge_pairs, mask="masked_global", dom="weak", k=_K, extra_places=()):
    edges, places = _graph(edge_pairs)
    if extra_places:
        places = pd.concat([places, pd.DataFrame(
            {"place_id": list(extra_places), "name_norm": list(extra_places),
             "lat_centroid": 35.5, "lon_centroid": 129.3})], ignore_index=True)
    d = m.compute_degree(edges, places)
    return m.compute_lifetime(edges, places, d, mask, dom, k)


def test_스타_중심은_L_K_cap():
    star = [("C", f"L{i}") for i in range(4)]
    l = _lifetime(star)
    assert int(l.loc["C"]) == _K          # 컴포넌트 지배자 = K (cap)
    assert int(l.loc["L0"]) == 0          # 잎: k=1 ego에 D=4 존재 → 미성립


def test_컴포넌트_소진_후_판정_고정_k5():
    star = [("C", f"L{i}") for i in range(3)]
    l = _lifetime(star, k=5)
    assert int(l.loc["C"]) == 5


def test_선형_체인_동률은_weak_strict_분기():
    chain = [("A", "B"), ("B", "C"), ("C", "D")]   # B·C 둘 다 D=2 (동률)
    weak = _lifetime(chain, dom="weak")
    strict = _lifetime(chain, dom="strict")
    assert int(weak.loc["B"]) == _K       # 공동 최고 인정(>=)
    assert int(strict.loc["B"]) == 0      # strict(>)는 동률 탈락


def test_더_큰_hub가_k3에_존재하면_L2():
    g = [("P", "x"), ("P", "y"), ("P", "a"), ("a", "b"), ("b", "H"),
         ("H", "h1"), ("H", "h2"), ("H", "h3")]    # D: P=3, H=4, 거리 3-hop
    l = _lifetime(g)
    assert int(l.loc["P"]) == 2


def test_D0은_L0():
    l = _lifetime([("A", "B")], extra_places=("Z",))
    assert int(l.loc["Z"]) == 0


def test_subgraph_변형은_masked와_다르게_동작():
    chain = [("A", "B"), ("B", "C")]
    # A: masked weak k=1 → D[A]=1 < D[B]=2 → L=0
    assert int(_lifetime(chain, mask="masked_global", dom="weak").loc["A"]) == 0
    # A: subgraph weak k=1 → ego{A,B} 유도 차수 A=1,B=1 동률 → 성립; k=2에 C 편입 → 탈락 → L=1
    assert int(_lifetime(chain, mask="subgraph", dom="weak").loc["A"]) == 1
    # B는 양 변형 모두 지배자
    assert int(_lifetime(chain, mask="subgraph", dom="weak").loc["B"]) == _K


def test_미지_변형은_ContractViolation():
    with pytest.raises(ContractViolation):
        _lifetime([("A", "B")], mask="global")
    with pytest.raises(ContractViolation):
        _lifetime([("A", "B")], dom="tie")


def test_L은_0_k_max_범위_정수():
    g = [("P", "x"), ("P", "y"), ("x", "y"), ("y", "z")]
    for mask in m.MASK_MODES:
        for dom in m.DOMINANCES:
            l = _lifetime(g, mask=mask, dom=dom, k=7)
            assert ((l >= 0) & (l <= 7)).all()
