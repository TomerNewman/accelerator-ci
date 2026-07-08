from __future__ import annotations

import json
import subprocess

import pytest

from accelerator_ci.operators.errors import OperatorError
from accelerator_ci.operators.install import (
    ensure_namespace,
    create_operator_group,
    create_subscription,
    approve_install_plan,
    is_operator_installed,
    wait_for_csv,
    wait_for_csv_by_name,
    wait_for_subscription_installed,
    wait_for_crd,
)


class MockOcRunner:
    def __init__(self):
        self.calls: list[tuple] = []
        self.apply_calls: list[str] = []
        self._responses: list[subprocess.CompletedProcess] = []
        self._apply_error: str | None = None

    def queue(self, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self._responses.append(subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr))

    def set_apply_error(self, msg: str) -> None:
        self._apply_error = msg

    def oc(self, *args, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append((args, kwargs))
        if self._responses:
            return self._responses.pop(0)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    def apply_yaml(self, yaml_content: str, timeout: int = 120) -> None:
        self.apply_calls.append(yaml_content)
        if self._apply_error:
            raise RuntimeError(self._apply_error)


@pytest.fixture
def _no_sleep(monkeypatch):
    import accelerator_ci.operators.install as mod
    clock = [0.0]

    def fake_monotonic():
        val = clock[0]
        clock[0] += 5
        return val

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)


def _csv_list(*csvs: tuple[str, str]) -> str:
    """Build JSON with CSV items. Each csv is (name, phase)."""
    items = []
    for name, phase in csvs:
        items.append({
            "metadata": {"name": name},
            "status": {"phase": phase},
        })
    return json.dumps({"items": items})


class TestIsOperatorInstalled:
    def test_found_and_succeeded(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_csv_list(("gpu-pkg.v1.2", "Succeeded")))
        assert is_operator_installed(oc, "ns", "gpu-pkg") is True

    def test_found_but_failed(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_csv_list(("gpu-pkg.v1.2", "Failed")))
        assert is_operator_installed(oc, "ns", "gpu-pkg") is False

    def test_found_but_installing(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_csv_list(("gpu-pkg.v1.2", "Installing")))
        assert is_operator_installed(oc, "ns", "gpu-pkg") is False

    def test_different_package(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_csv_list(("other-pkg.v1.0", "Succeeded")))
        assert is_operator_installed(oc, "ns", "gpu-pkg") is False

    def test_no_csvs(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=json.dumps({"items": []}))
        assert is_operator_installed(oc, "ns", "gpu-pkg") is False

    def test_api_error(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        assert is_operator_installed(oc, "ns", "gpu-pkg") is False

    def test_bad_json(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="not json")
        assert is_operator_installed(oc, "ns", "gpu-pkg") is False

    def test_multiple_csvs_one_match(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_csv_list(
            ("nfd.v1.0", "Succeeded"),
            ("gpu-pkg.v2.0", "Succeeded"),
        ))
        assert is_operator_installed(oc, "ns", "gpu-pkg") is True

    def test_no_false_positive_on_prefix_overlap(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_csv_list(("gpu-pkg-extra.v1.0", "Succeeded")))
        assert is_operator_installed(oc, "ns", "gpu-pkg") is False


class TestEnsureNamespace:
    def test_already_exists(self):
        oc = MockOcRunner()
        oc.queue(rc=0)
        ensure_namespace(oc, "gpu-operator")
        assert len(oc.calls) == 1

    def test_created_when_missing(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        oc.queue(rc=0)
        ensure_namespace(oc, "gpu-operator")
        assert oc.calls[1][0] == ("create", "namespace", "gpu-operator")

    def test_create_failure_raises(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        oc.queue(rc=1, stderr="forbidden")
        with pytest.raises(OperatorError, match="Failed to create namespace"):
            ensure_namespace(oc, "gpu-operator")

    def test_create_passes_retries_zero(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        oc.queue(rc=0)
        ensure_namespace(oc, "test-ns")
        assert oc.calls[1][1].get("retries") == 0


class TestCreateOperatorGroup:
    def test_single_namespace_target(self):
        oc = MockOcRunner()
        create_operator_group(oc, "ns1", "og1")
        m = json.loads(oc.apply_calls[0])
        assert m["kind"] == "OperatorGroup"
        assert m["metadata"]["namespace"] == "ns1"
        assert m["spec"]["targetNamespaces"] == ["ns1"]

    def test_all_namespaces(self):
        oc = MockOcRunner()
        create_operator_group(oc, "ns1", "og1", all_namespaces=True)
        m = json.loads(oc.apply_calls[0])
        assert m["spec"] == {}

    def test_apply_failure_propagates(self):
        oc = MockOcRunner()
        oc.set_apply_error("server error")
        with pytest.raises(RuntimeError, match="server error"):
            create_operator_group(oc, "ns1", "og1")


class TestCreateSubscription:
    def test_automatic_approval(self):
        oc = MockOcRunner()
        create_subscription(oc, "ns", "sub1", "pkg", "catalog", "stable")
        spec = json.loads(oc.apply_calls[0])["spec"]
        assert spec["installPlanApproval"] == "Automatic"
        assert spec["name"] == "pkg"
        assert spec["source"] == "catalog"
        assert spec["channel"] == "stable"
        assert spec["sourceNamespace"] == "openshift-marketplace"
        assert "startingCSV" not in spec

    def test_manual_with_starting_csv(self):
        oc = MockOcRunner()
        create_subscription(
            oc, "ns", "sub1", "pkg", "cat", "stable",
            starting_csv="pkg.v1.0", manual_approval=True,
        )
        spec = json.loads(oc.apply_calls[0])["spec"]
        assert spec["installPlanApproval"] == "Manual"
        assert spec["startingCSV"] == "pkg.v1.0"


def _ip_list(csv_name: str, approved: bool, ip_name: str = "ip-1") -> str:
    return json.dumps({"items": [{
        "metadata": {"name": ip_name},
        "spec": {"clusterServiceVersionNames": [csv_name], "approved": approved},
    }]})


@pytest.mark.usefixtures("_no_sleep")
class TestApproveInstallPlan:
    def test_approves_matching_plan(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_ip_list("csv.v1", False, "plan-abc"))
        oc.queue(rc=0)
        approve_install_plan(oc, "ns", "csv.v1", timeout=30)
        assert oc.calls[1][0][:3] == ("patch", "installplan", "plan-abc")

    def test_skips_already_approved_and_waits(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_ip_list("csv.v1", True))
        oc.queue(rc=0, stdout=_ip_list("csv.v1", False))
        oc.queue(rc=0)
        approve_install_plan(oc, "ns", "csv.v1", timeout=30)

    def test_timeout(self):
        oc = MockOcRunner()
        with pytest.raises(OperatorError, match="Timeout"):
            approve_install_plan(oc, "ns", "csv.v1", timeout=1)

    def test_retries_after_patch_failure(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_ip_list("csv.v1", False))
        oc.queue(rc=1, stderr="conflict")
        oc.queue(rc=0, stdout=_ip_list("csv.v1", False))
        oc.queue(rc=0)
        approve_install_plan(oc, "ns", "csv.v1", timeout=30)

    def test_waits_on_api_error(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        oc.queue(rc=0, stdout=_ip_list("csv.v1", False))
        oc.queue(rc=0)
        approve_install_plan(oc, "ns", "csv.v1", timeout=30)


@pytest.mark.usefixtures("_no_sleep")
class TestWaitForCsv:
    def test_all_succeeded(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="Succeeded Succeeded")
        wait_for_csv(oc, "ns", timeout=30)

    def test_failed_raises(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="Succeeded Failed")
        oc.queue(rc=0, stdout="yaml details")
        with pytest.raises(OperatorError, match="failed"):
            wait_for_csv(oc, "ns", timeout=30)

    def test_installing_then_succeeded(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="Installing")
        oc.queue(rc=0, stdout="Succeeded")
        wait_for_csv(oc, "ns", timeout=30)

    def test_timeout(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="Installing")
        with pytest.raises(OperatorError, match="Timeout"):
            wait_for_csv(oc, "ns", timeout=1)

    def test_empty_phases_waits(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="")
        oc.queue(rc=0, stdout="Succeeded")
        wait_for_csv(oc, "ns", timeout=30)

    def test_api_error_retries(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        oc.queue(rc=0, stdout="Succeeded")
        wait_for_csv(oc, "ns", timeout=30)


def _sub_json(installed_csv: str = "", conditions: list | None = None) -> str:
    status: dict = {}
    if installed_csv:
        status["installedCSV"] = installed_csv
    if conditions:
        status["conditions"] = conditions
    return json.dumps({"status": status})


@pytest.mark.usefixtures("_no_sleep")
class TestWaitForSubscriptionInstalled:
    def test_returns_installed_csv(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_sub_json(installed_csv="pkg.v1.2.3"))
        assert wait_for_subscription_installed(oc, "ns", "my-sub", timeout=30) == "pkg.v1.2.3"

    def test_resolution_failed_raises(self):
        oc = MockOcRunner()
        conds = [{"type": "ResolutionFailed", "status": "True", "message": "no match"}]
        oc.queue(rc=0, stdout=_sub_json(conditions=conds))
        with pytest.raises(OperatorError, match="no match"):
            wait_for_subscription_installed(oc, "ns", "my-sub", timeout=30)

    def test_waits_until_csv_appears(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_sub_json())
        oc.queue(rc=0, stdout=_sub_json(installed_csv="pkg.v1.0"))
        assert wait_for_subscription_installed(oc, "ns", "my-sub", timeout=30) == "pkg.v1.0"

    def test_timeout(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=_sub_json())
        with pytest.raises(OperatorError, match="Timeout"):
            wait_for_subscription_installed(oc, "ns", "my-sub", timeout=1)

    def test_api_error_retries(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        oc.queue(rc=0, stdout=_sub_json(installed_csv="pkg.v2"))
        assert wait_for_subscription_installed(oc, "ns", "my-sub", timeout=30) == "pkg.v2"


@pytest.mark.usefixtures("_no_sleep")
class TestWaitForCsvByName:
    def test_succeeded(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="Succeeded")
        wait_for_csv_by_name(oc, "ns", "gpu-operator.v24.3", timeout=30)

    def test_failed_raises(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="Failed")
        oc.queue(rc=0, stdout="yaml output")
        with pytest.raises(OperatorError, match="failed"):
            wait_for_csv_by_name(oc, "ns", "gpu-operator.v24.3", timeout=30)

    def test_installing_then_succeeded(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="Installing")
        oc.queue(rc=0, stdout="Succeeded")
        wait_for_csv_by_name(oc, "ns", "csv.v1", timeout=30)

    def test_timeout(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        with pytest.raises(OperatorError, match="Timeout"):
            wait_for_csv_by_name(oc, "ns", "csv.v1", timeout=1)

    def test_api_error_retries(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        oc.queue(rc=0, stdout="Succeeded")
        wait_for_csv_by_name(oc, "ns", "csv.v1", timeout=30)


@pytest.mark.usefixtures("_no_sleep")
class TestWaitForCrd:
    def test_established(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="True")
        wait_for_crd(oc, "gpus.nvidia.com", timeout=30)

    def test_not_yet_then_established(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="False")
        oc.queue(rc=0, stdout="True")
        wait_for_crd(oc, "gpus.nvidia.com", timeout=30)

    def test_timeout(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        with pytest.raises(OperatorError, match="Timeout"):
            wait_for_crd(oc, "gpus.nvidia.com", timeout=1)

    def test_crd_missing_then_appears(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        oc.queue(rc=0, stdout="True")
        wait_for_crd(oc, "gpus.nvidia.com", timeout=30)
