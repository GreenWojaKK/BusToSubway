# DIFF 3값 판정기 + measured 고정 부패 감지 + 스텁 생성 (verification.md §2.3)
from bts.checks import diff


BASE = {"before": {"canonical": {"rows": 379,
                                 "trips": {"short_turn": 227, "circular": 2271}}}}
KDS = [diff.KD(id="KD-0002", check="D-S01-B-005", prior=227, measured=319,
               status="hypothesis", doc="docs/investigations/DIFF-0001-variant-role-trips.md")]


def test_MATCH(tmp_path):
    r = diff.judge("D-S01-B-001", 379, "before.canonical.rows",
                   baseline=BASE, kds=[], stub_dir=tmp_path)
    assert r.status == "MATCH"
    assert not list(tmp_path.glob("*.md"))          # 스텁 없음


def test_EXPLAINED_등재_measured_일치(tmp_path):
    r = diff.judge("D-S01-B-005", 319, "before.canonical.trips.short_turn",
                   baseline=BASE, kds=KDS, stub_dir=tmp_path)
    assert r.status == "EXPLAINED"
    assert "KD-0002" in r.note and "hypothesis" in r.note
    assert not list(tmp_path.glob("*.md"))


def test_measured_재이탈은_UNEXPLAINED_강등(tmp_path):
    # 시나리오: known_deviation의 measured 재이탈 (319→320) — 설명의 부패 감지
    r = diff.judge("D-S01-B-005", 320, "before.canonical.trips.short_turn",
                   baseline=BASE, kds=KDS, stub_dir=tmp_path)
    assert r.status == "UNEXPLAINED"
    assert "재이탈" in r.note
    assert len(list(tmp_path.glob("DIFF-*.md"))) == 1   # 규명 스텁 자동 생성


def test_미등재_편차는_UNEXPLAINED_스텁(tmp_path):
    r = diff.judge("D-S01-B-004", 2280, "before.canonical.trips.circular",
                   baseline=BASE, kds=[], stub_dir=tmp_path, metric="circular-trips")
    assert r.status == "UNEXPLAINED"
    stubs = list(tmp_path.glob("DIFF-0001-circular-trips.md"))
    assert len(stubs) == 1
    text = stubs[0].read_text(encoding="utf-8")
    assert "2280" in text and "2271" in text            # 측정값·기준값이 미리 채워진 템플릿


def test_같은_metric_스텁은_재사용(tmp_path):
    diff.judge("D-X", 1, "before.canonical.rows", baseline=BASE, kds=[],
               stub_dir=tmp_path, metric="m1")
    diff.judge("D-X", 2, "before.canonical.rows", baseline=BASE, kds=[],
               stub_dir=tmp_path, metric="m1")
    assert len(list(tmp_path.glob("DIFF-*.md"))) == 1   # 중복 발번 방지


def test_tol_허용_오차(tmp_path):
    r = diff.judge("D-X", 380, "before.canonical.rows", tol=1,
                   baseline=BASE, kds=[], stub_dir=tmp_path)
    assert r.status == "MATCH"


def test_동결층_실파일_로드():
    # 실제 동결층 3파일이 판정기 계약대로 읽히는지 (Stage 1 등재분 KD-0001~0004 확인).
    # 대장은 추가 전용(append-only)이다 — 후속 스테이지 등재(KD-0005~: s02 DIFF-0002 등)를
    # 막지 않도록 '선두 4건 + id 유일'만 고정한다 (전체 목록 동결은 append-only 설계와 모순).
    baseline = diff.load_baseline()
    assert diff.baseline_value(baseline, "before.canonical.rows") == 379
    assert diff.baseline_value(baseline, "before.trips") == 7625
    kds = diff.load_known_deviations()
    ids = [k.id for k in kds]
    assert ids[:4] == ["KD-0001", "KD-0002", "KD-0003", "KD-0004"]
    assert len(ids) == len(set(ids))                    # id 중복 없음
    assert all(k.status in ("hypothesis", "confirmed") for k in kds)
    # role trips 3건은 hypothesis가 계약 (사실 왜곡 금지 — design.md §6.3)
    assert all(k.status == "hypothesis" for k in kds if k.id in ("KD-0001", "KD-0002", "KD-0003"))
