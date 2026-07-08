from __future__ import annotations

import subprocess

import pytest

from accelerator_ci.operators.errors import OperatorError
from accelerator_ci.operators.prerequisites import (
    verify_required_operators,
    configure_internal_registry,
)


class MockOcRunner:
    def __init__(self):
        self._responses: list[subprocess.CompletedProcess] = []

    def queue(self, rc: int = 0, stdout: str = "") -> None:
        self._responses.append(subprocess.CompletedProcess([], rc, stdout=stdout, stderr=""))

    def oc(self, *args, **kwargs) -> subprocess.CompletedProcess:
        if self._responses:
            return self._responses.pop(0)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")


@pytest.fixture
def _no_sleep(monkeypatch):
    import accelerator_ci.operators.prerequisites as mod
    clock = [0.0]

    def fake_monotonic():
        val = clock[0]
        clock[0] += 20
        return val

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)


ALL_RUNNING = (
    "openshift-service-ca  service-ca-xyz  1/1  Running\n"
    "openshift-operator-lifecycle-manager  olm-abc  1/1  Running\n"
    "openshift-machine-config-operator  machine-config-xyz  1/1  Running\n"
    "openshift-image-registry  image-registry-abc  1/1  Running\n"
)


@pytest.mark.usefixtures("_no_sleep")
class TestVerifyRequiredOperators:
    def test_all_present(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout=ALL_RUNNING)
        verify_required_operators(oc, timeout=60)

    def test_missing_then_appears(self):
        oc = MockOcRunner()
        partial = "openshift-service-ca  service-ca-xyz  1/1  Running\n"
        oc.queue(rc=0, stdout=partial)
        oc.queue(rc=0, stdout=ALL_RUNNING)
        verify_required_operators(oc, timeout=60)

    def test_api_unreachable_then_ok(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        oc.queue(rc=0, stdout=ALL_RUNNING)
        verify_required_operators(oc, timeout=60)

    def test_timeout(self):
        oc = MockOcRunner()
        oc.queue(rc=1)
        with pytest.raises(OperatorError, match="Timeout"):
            verify_required_operators(oc, timeout=1)


@pytest.mark.usefixtures("_no_sleep")
class TestConfigureInternalRegistry:
    def test_success(self):
        oc = MockOcRunner()
        oc.queue(rc=0)  # storage patch
        oc.queue(rc=0)  # managed patch
        oc.queue(rc=0, stdout="image-registry-pod  1/1  Running")
        configure_internal_registry(oc, timeout=60)

    def test_storage_patch_fails(self):
        oc = MockOcRunner()
        oc.queue(rc=1, stdout="error")
        with pytest.raises(OperatorError, match="storage"):
            configure_internal_registry(oc, timeout=60)

    def test_managed_patch_fails(self):
        oc = MockOcRunner()
        oc.queue(rc=0)  # storage ok
        oc.queue(rc=1, stdout="error")
        with pytest.raises(OperatorError, match="managementState"):
            configure_internal_registry(oc, timeout=60)

    def test_registry_pod_not_running_timeout(self):
        oc = MockOcRunner()
        oc.queue(rc=0)
        oc.queue(rc=0)
        oc.queue(rc=0, stdout="Pending")
        with pytest.raises(OperatorError, match="Timeout"):
            configure_internal_registry(oc, timeout=1)
