from __future__ import annotations

import subprocess

import pytest

from accelerator_ci.operators.errors import OperatorError
from accelerator_ci.operators.cluster_health import (
    wait_for_cluster_stability,
    wait_for_mcp_updated,
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
    import accelerator_ci.operators.cluster_health as mod
    clock = [0.0]

    def fake_monotonic():
        val = clock[0]
        clock[0] += 25
        return val

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)


@pytest.mark.usefixtures("_no_sleep")
class TestWaitForClusterStability:
    def test_all_healthy(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="node-1   True\n")
        oc.queue(rc=0, stdout="console   True   False   False\n")
        wait_for_cluster_stability(oc, timeout=100)

    def test_node_not_ready_then_recovers(self):
        oc = MockOcRunner()
        # first poll: node not ready
        oc.queue(rc=0, stdout="node-1   False\n")
        oc.queue(rc=0, stdout="console   True   False   False\n")
        # second poll: all good
        oc.queue(rc=0, stdout="node-1   True\n")
        oc.queue(rc=0, stdout="console   True   False   False\n")
        wait_for_cluster_stability(oc, timeout=100)

    def test_co_degraded_then_recovers(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="node-1   True\n")
        oc.queue(rc=0, stdout="dns   True   False   True\n")
        oc.queue(rc=0, stdout="node-1   True\n")
        oc.queue(rc=0, stdout="dns   True   False   False\n")
        wait_for_cluster_stability(oc, timeout=100)

    def test_api_unreachable_then_recovers(self):
        oc = MockOcRunner()
        oc.queue(rc=1)  # API down
        oc.queue(rc=0, stdout="node-1   True\n")
        oc.queue(rc=0, stdout="console   True   False   False\n")
        wait_for_cluster_stability(oc, timeout=100)

    def test_timeout(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="node-1   False\n")
        oc.queue(rc=0, stdout="console   True   False   False\n")
        with pytest.raises(OperatorError, match="did not stabilize"):
            wait_for_cluster_stability(oc, timeout=1)

    def test_co_progressing_waits(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="node-1   True\n")
        oc.queue(rc=0, stdout="ingress   True   True   False\n")
        oc.queue(rc=0, stdout="node-1   True\n")
        oc.queue(rc=0, stdout="ingress   True   False   False\n")
        wait_for_cluster_stability(oc, timeout=100)


@pytest.mark.usefixtures("_no_sleep")
class TestWaitForMcpUpdated:
    def test_already_updated_waits_for_mco(self):
        """MCP shows Updated=True immediately — function waits to guard against
        MCO not having started yet, then accepts on second pass."""
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="worker   True   False   False\n")
        oc.queue(rc=0, stdout="worker   True   False   False\n")
        oc.queue(rc=0, stdout="worker   True   False   False\n")
        wait_for_mcp_updated(oc, timeout=200)

    def test_updating_then_updated(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="worker   False   True   False\n")
        oc.queue(rc=0, stdout="worker   True   False   False\n")
        wait_for_mcp_updated(oc, timeout=100)

    def test_degraded_raises_immediately(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="worker   False   False   True\n")
        with pytest.raises(OperatorError, match="Degraded"):
            wait_for_mcp_updated(oc, timeout=100)

    def test_api_down_during_reboot(self):
        oc = MockOcRunner()
        oc.queue(rc=1)  # API down = saw_updating=True
        oc.queue(rc=0, stdout="worker   True   False   False\n")
        wait_for_mcp_updated(oc, timeout=100)

    def test_timeout(self):
        oc = MockOcRunner()
        oc.queue(rc=0, stdout="worker   False   True   False\n")
        with pytest.raises(OperatorError, match="did not finish"):
            wait_for_mcp_updated(oc, timeout=1)
