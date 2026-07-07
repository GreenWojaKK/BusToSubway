# DIFF 판정기 테스트 — MATCH/EXPLAINED/UNEXPLAINED와 조사 메모 생성을 확인한다.
from bts.checks import diff


BASE = {"before": {"canonical": {"rows": 379,
                                 "trips": {"short_turn": 227, "circular": 2271}}}}
KDS = [diff.KD(id="KD-0002", check="D-S01-B-005", prior=227, measured=319,
               status="hypothesis", doc="docs/investigations/DIFF-0001-variant-role-trips.md")]


def test_MATCH(tmp_path):
    r = diff.judge("D-S01-B-001", 379, "before.canonical.rows",
                   baseline=BASE, kds=[], stub_dir=tmp_path)
    assert r.status == "MATCH"
    assert not list(tmp_path.glob("*.md"))          # 조사 메모를 만들지 않는다.


def test_EXPLAINED_등재_measured_일치(tmp_path):
    r = diff.judge("D-S01-B-005", 319, "before.canonical.trips.short_turn",
                   baseline=BASE, kds=KDS, stub_dir=tmp_path)
    assert r.status == "EXPLAINED"
    assert "KD-0002" in r.note and "hypothesis" in r.note
    assert not list(tmp_path.glob("*.md"))


def test_measured_재이탈은_UNEXPLAINED_강등(tmp_path):
    # 시나리오: known_deviation의 measured가 다시 달라지면 기존 설명을 재검토해야 한다.
    r = diff.judge("D-S01-B-005", 320, "before.canonical.trips.short_turn",
                   baseline=BASE, kds=KDS, stub_dir=tmp_path)
    assert r.status == "UNEXPLAINED"
    assert "재이탈" in r.note
    assert len(list(tmp_path.glob("DIFF-*.md"))) == 1   # 조사 메모를 자동 생성한다.


def test_unregistered_diff_creates_investigation_note(tmp_path):
    r = diff.judge("D-S01-B-004", 2280, "before.canonical.trips.circular",
                   baseline=BASE, kds=[], stub_dir=tmp_path, metric="circular-trips")
    assert r.status == "UNEXPLAINED"
    stubs = list(tmp_path.glob("DIFF-0001-circular-trips.md"))
    assert len(stubs) == 1
    text = stubs[0].read_text(encoding="utf-8")
    assert "2280" in text and "2271" in text            # 조사 메모에 observed/expected가 들어간다.


def test_same_metric_reuses_investigation_note(tmp_path):
    diff.judge("D-X", 1, "before.canonical.rows", baseline=BASE, kds=[],
               stub_dir=tmp_path, metric="m1")
    diff.judge("D-X", 2, "before.canonical.rows", baseline=BASE, kds=[],
               stub_dir=tmp_path, metric="m1")
    assert len(list(tmp_path.glob("DIFF-*.md"))) == 1   # 중복 발번 방지


def test_tol_허용_오차(tmp_path):
    r = diff.judge("D-X", 380, "before.canonical.rows", tol=1,
                   baseline=BASE, kds=[], stub_dir=tmp_path)
    assert r.status == "MATCH"


def test_reference_files_load():
    # 실제 reference 3파일을 판정기가 계약대로 읽는지 확인한다.
    # 대장은 추가 전용(append-only)이다 — 후속 스테이지 등재(KD-0005~: s02 DIFF-0002 등)를
    # 후속 항목 추가를 막지 않도록 선두 4건과 id 유일성만 고정한다.
    reference_values = diff.load_reference_values()
    assert diff.reference_value(reference_values, "before.canonical.rows") == 379
    assert diff.reference_value(reference_values, "before.trips") == 7625
    kds = diff.load_known_deviations()
    ids = [k.id for k in kds]
    assert ids[:4] == ["KD-0001", "KD-0002", "KD-0003", "KD-0004"]
    assert len(ids) == len(set(ids))                    # id 중복 없음
    assert all(k.status in ("hypothesis", "confirmed") for k in kds)
    # role trips 3건은 hypothesis가 계약 (사실 왜곡 금지 — design.md §6.3)
    assert all(k.status == "hypothesis" for k in kds if k.id in ("KD-0001", "KD-0002", "KD-0003"))
