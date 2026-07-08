from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from accelerator_ci.testing.helpers import (
    delete_pod_if_exists,
    wait_for_pod_done,
    check_pending_pod_errors,
    describe_pod_status,
    FATAL_WAITING_REASONS,
)


def _api_exc(status: int):
    from kubernetes.client.rest import ApiException
    return ApiException(status=status)


def _pod(phase="Running", container_statuses=None, init_container_statuses=None, conditions=None):
    status = SimpleNamespace(
        phase=phase,
        container_statuses=container_statuses,
        init_container_statuses=init_container_statuses,
        conditions=conditions,
    )
    return SimpleNamespace(status=status)


@pytest.fixture
def _no_sleep(monkeypatch):
    import accelerator_ci.testing.helpers as mod
    clock = [0.0]

    def fake_monotonic():
        val = clock[0]
        clock[0] += 10
        return val

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)


@pytest.mark.usefixtures("_no_sleep")
class TestDeletePodIfExists:
    def test_pod_not_found(self):
        api = MagicMock()
        api.delete_namespaced_pod.side_effect = _api_exc(404)
        delete_pod_if_exists(api, "p", "ns")

    def test_pod_deleted(self):
        api = MagicMock()
        api.read_namespaced_pod.side_effect = _api_exc(404)
        delete_pod_if_exists(api, "p", "ns")

    def test_timeout(self):
        api = MagicMock()
        api.read_namespaced_pod.return_value = _pod()
        with pytest.raises(TimeoutError, match="not deleted"):
            delete_pod_if_exists(api, "p", "ns", timeout=1)


@pytest.mark.usefixtures("_no_sleep")
class TestWaitForPodDone:
    def test_succeeded(self):
        api = MagicMock()
        api.read_namespaced_pod.return_value = _pod("Succeeded")
        assert wait_for_pod_done(api, "p", "ns") == "Succeeded"

    def test_failed(self):
        api = MagicMock()
        api.read_namespaced_pod.return_value = _pod("Failed")
        assert wait_for_pod_done(api, "p", "ns") == "Failed"

    def test_timeout(self):
        api = MagicMock()
        api.read_namespaced_pod.return_value = _pod("Running")
        with pytest.raises(TimeoutError, match="did not complete"):
            wait_for_pod_done(api, "p", "ns", timeout=1)

    def test_pending_with_fatal_error(self):
        waiting = SimpleNamespace(reason="ImagePullBackOff", message="pull failed")
        cs = SimpleNamespace(state=SimpleNamespace(waiting=waiting))
        api = MagicMock()
        api.read_namespaced_pod.return_value = _pod("Pending", container_statuses=[cs])
        with pytest.raises(RuntimeError, match="ImagePullBackOff"):
            wait_for_pod_done(api, "p", "ns")


class TestCheckPendingPodErrors:
    def test_no_errors(self):
        pod = _pod("Pending", container_statuses=[], conditions=[])
        check_pending_pod_errors(pod, "p", "ns")

    @pytest.mark.parametrize("reason", list(FATAL_WAITING_REASONS))
    def test_fatal_waiting(self, reason):
        waiting = SimpleNamespace(reason=reason, message="details")
        cs = SimpleNamespace(state=SimpleNamespace(waiting=waiting))
        pod = _pod("Pending", container_statuses=[cs])
        with pytest.raises(RuntimeError, match=reason):
            check_pending_pod_errors(pod, "p", "ns")

    def test_unschedulable(self):
        cond = SimpleNamespace(type="PodScheduled", status="False",
                               reason="Unschedulable", message="no gpu nodes")
        pod = _pod("Pending", container_statuses=[], conditions=[cond])
        with pytest.raises(RuntimeError, match="cannot be scheduled"):
            check_pending_pod_errors(pod, "p", "ns")

    def test_init_container_fatal(self):
        waiting = SimpleNamespace(reason="ErrImagePull", message="not found")
        cs = SimpleNamespace(state=SimpleNamespace(waiting=waiting))
        pod = _pod("Pending", container_statuses=[], init_container_statuses=[cs])
        with pytest.raises(RuntimeError, match="ErrImagePull"):
            check_pending_pod_errors(pod, "p", "ns")


class TestDescribePodStatus:
    def test_no_detail(self):
        pod = _pod("Running", container_statuses=[], conditions=[])
        assert describe_pod_status(pod) == "no additional detail available"

    def test_waiting_container(self):
        waiting = SimpleNamespace(reason="CrashLoop", message="back-off")
        cs = SimpleNamespace(name="main", state=SimpleNamespace(waiting=waiting))
        pod = _pod("Running", container_statuses=[cs])
        result = describe_pod_status(pod)
        assert "main" in result
        assert "CrashLoop" in result

    def test_false_condition(self):
        cond = SimpleNamespace(type="Ready", status="False", reason="NotReady", message="containers not ready")
        pod = _pod("Running", container_statuses=[], conditions=[cond])
        assert "Ready" in describe_pod_status(pod)
