# 공용 fixture — runner와 manifest 테스트가 임시 artifacts/runs 안에서 실행되게 한다.
import pandas as pd
import pytest

import bts.paths as paths
import bts.run as run
from bts.checks import core, diff


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """artifacts/·runs/를 tmp로 돌리고 더미 스테이지를 registry에 등록한다.

    실제 스테이지 구현 대신 더미 스테이지를 써서 runner의 버전 생성,
    review 요구, 반환 코드 처리를 확인한다.
    """
    monkeypatch.setattr(paths, "ARTIFACTS", tmp_path / "artifacts")
    monkeypatch.setattr(paths, "RUNS", tmp_path / "runs")
    stub_dir = tmp_path / "investigations"

    def dummy_build(inputs, params, vdir):
        if params.get("mode") == "build_contract_violation":
            from bts.io import ContractViolation
            raise ContractViolation("로더 계약 위반 주입 (테스트)")
        df = pd.DataFrame({"stop_id": ["P1", "P2", "P3"],
                           "v": [params.get("v", 1)] * 3})
        df.to_csv(vdir / "out.csv", index=False, encoding="utf-8-sig")
        return {"out.csv": vdir / "out.csv"}

    def dummy_checks(ctx):
        # mode는 '+'로 조합할 수 있다. 예: "physical_fail+diff_unexplained".
        mode = ctx.params.get("mode", "ok")
        results = [core.row_count(
            "C-DUM-X-001", "CONTRACT", ctx.df("out.csv"),
            3 if "contract_fail" not in mode else 4, "test-fixture")]
        if "physical_fail" in mode:
            results.append(core.check_true(
                "P-DUM-X-001", "PHYSICAL", False, "관측", "기대", "test-fixture"))
        if "diff_unexplained" in mode:
            results.append(diff.judge(
                "D-DUM-X-001", 380, "before.canonical.rows",
                baseline={"before": {"canonical": {"rows": 379}}},
                kds=[], stub_dir=stub_dir, metric="dummy-canonical-rows"))
        return results

    dummy = run.Stage(builders={"before": dummy_build},
                      checks={"before": dummy_checks},
                      inputs=[], scopes=("before",))
    dummy2 = run.Stage(builders={"before": dummy_build},
                       checks={"before": dummy_checks},
                       inputs=["t90_dummy"], scopes=("before",))
    monkeypatch.setitem(run.REGISTRY, "t90_dummy", dummy)
    monkeypatch.setitem(run.REGISTRY, "t91_downstream", dummy2)

    state = {"params": {"t90_dummy": {}, "t91_downstream": {}}}
    monkeypatch.setattr(run, "stage_params",
                        lambda s: state["params"].get(s, {}))
    return {"tmp": tmp_path, "stub_dir": stub_dir, "state": state}
