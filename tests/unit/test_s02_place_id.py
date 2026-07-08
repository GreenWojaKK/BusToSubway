# make_place_id 결정성 테스트 (stage2_place_hub_spec.md §6.1 test_place_id)
import re

import pytest

from dataio import ContractViolation
from s02_place import merge


def test_멤버_순서_무관_결정성():
    a = merge.make_place_id("before", "가", ["P2", "P1", "P3"])
    b = merge.make_place_id("before", "가", ["P1", "P3", "P2"])
    assert a == b


def test_scope_접두_PB_PA_와_8hex():
    b = merge.make_place_id("before", "가", ["P1"])
    a = merge.make_place_id("after", "가", ["P1"])
    assert re.fullmatch(r"PB_[0-9a-f]{8}", b)
    assert re.fullmatch(r"PA_[0-9a-f]{8}", a)
    assert b[3:] == a[3:]                                 # 해시부는 scope 무관(콘텐츠 파생)


def test_이름_또는_최소멤버가_다르면_id가_다르다():
    assert merge.make_place_id("before", "가", ["P1"]) != merge.make_place_id("before", "나", ["P1"])
    assert merge.make_place_id("before", "가", ["P1", "P2"]) \
        != merge.make_place_id("before", "가", ["P2", "P3"])


def test_빈_멤버는_거부():
    with pytest.raises(ContractViolation):
        merge.make_place_id("before", "가", [])
