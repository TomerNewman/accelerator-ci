from __future__ import annotations

import json
import subprocess

import pytest

from accelerator_ci.cluster_provision.status import (
    get_cluster_version,
    get_node_status,
    get_installed_operators,
    get_gpu_resources,
    print_status,
)


class FakeOcRunner:
    """Minimal OcRunner stub that returns pre-configured responses."""

    def __init__(self, responses: dict[str, subprocess.CompletedProcess] | None = None):
        self._responses = responses or {}
        self._default = subprocess.CompletedProcess([], 0, stdout="", stderr="")

    def oc(self, *args, **kwargs) -> subprocess.CompletedProcess:
        key = " ".join(args[:3])
        return self._responses.get(key, self._default)

    def apply_yaml(self, yaml_content, timeout=120):
        raise NotImplementedError


def _ok(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def _fail(stderr: str = "error") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, stdout="", stderr=stderr)


def _node_json(*nodes: dict) -> str:
    """Build a minimal oc get nodes -o json payload."""
    items = []
    for n in nodes:
        labels = {"kubernetes.io/hostname": n["name"]}
        for role in n.get("roles", []):
            labels[f"node-role.kubernetes.io/{role}"] = ""
        conditions = [{"type": "Ready", "status": "True" if n.get("ready", True) else "False"}]
        items.append({
            "metadata": {"name": n["name"], "labels": labels},
            "status": {
                "conditions": conditions,
                "nodeInfo": {"kubeletVersion": n.get("version", "v1.29.6")},
                "allocatable": n.get("allocatable", {"cpu": "8"}),
            },
        })
    return json.dumps({"items": items})


class TestGetClusterVersion:
    def test_returns_version(self):
        oc = FakeOcRunner({"get clusterversion version": _ok("4.16.3")})
        assert get_cluster_version(oc) == "4.16.3"

    def test_api_unreachable(self):
        oc = FakeOcRunner({"get clusterversion version": _fail("connection refused")})
        result = get_cluster_version(oc)
        assert "unavailable" in result

    def test_empty_output(self):
        oc = FakeOcRunner({"get clusterversion version": _ok("")})
        assert get_cluster_version(oc) == "(unknown)"


class TestGetNodeStatus:
    def test_single_node(self):
        data = _node_json({"name": "master-0", "roles": ["master"], "ready": True, "version": "v1.29.6+aaaa"})
        oc = FakeOcRunner({"get nodes -o": _ok(data)})
        nodes = get_node_status(oc)
        assert len(nodes) == 1
        assert nodes[0]["name"] == "master-0"
        assert nodes[0]["ready"] is True
        assert nodes[0]["roles"] == "master"
        assert nodes[0]["version"] == "v1.29.6+aaaa"

    def test_node_not_ready(self):
        data = _node_json({"name": "worker-1", "roles": ["worker"], "ready": False})
        oc = FakeOcRunner({"get nodes -o": _ok(data)})
        nodes = get_node_status(oc)
        assert nodes[0]["ready"] is False

    def test_multiple_roles(self):
        data = _node_json({"name": "combo-0", "roles": ["master", "worker"]})
        oc = FakeOcRunner({"get nodes -o": _ok(data)})
        nodes = get_node_status(oc)
        assert nodes[0]["roles"] == "master,worker"

    def test_no_roles(self):
        data = _node_json({"name": "bare-0", "roles": []})
        oc = FakeOcRunner({"get nodes -o": _ok(data)})
        nodes = get_node_status(oc)
        assert nodes[0]["roles"] == "<none>"

    def test_multiple_nodes(self):
        data = _node_json(
            {"name": "master-0", "roles": ["master"], "ready": True},
            {"name": "worker-0", "roles": ["worker"], "ready": True},
            {"name": "worker-1", "roles": ["worker"], "ready": False},
        )
        oc = FakeOcRunner({"get nodes -o": _ok(data)})
        nodes = get_node_status(oc)
        assert len(nodes) == 3

    def test_api_unreachable(self):
        oc = FakeOcRunner({"get nodes -o": _fail()})
        assert get_node_status(oc) == []

    def test_bad_json(self):
        oc = FakeOcRunner({"get nodes -o": _ok("not json")})
        assert get_node_status(oc) == []


class TestGetInstalledOperators:
    def test_operators_found(self):
        stdout = (
            "openshift-nfd   nfd-operator.v4.16.0   Succeeded\n"
            "nvidia-gpu      gpu-operator.v24.3.0   Succeeded\n"
        )
        oc = FakeOcRunner({"get csv -A": _ok(stdout)})
        ops = get_installed_operators(oc)
        assert len(ops) == 2
        assert ops[0]["name"] == "nfd-operator.v4.16.0"
        assert ops[0]["namespace"] == "openshift-nfd"
        assert ops[0]["phase"] == "Succeeded"

    def test_failed_operator(self):
        stdout = "ns   some-operator.v1.0   Failed\n"
        oc = FakeOcRunner({"get csv -A": _ok(stdout)})
        ops = get_installed_operators(oc)
        assert ops[0]["phase"] == "Failed"

    def test_no_operators(self):
        oc = FakeOcRunner({"get csv -A": _ok("")})
        assert get_installed_operators(oc) == []

    def test_api_unreachable(self):
        oc = FakeOcRunner({"get csv -A": _fail()})
        assert get_installed_operators(oc) == []


class TestGetGpuResources:
    def test_gpu_detected(self):
        data = _node_json({"name": "worker-0", "allocatable": {"cpu": "16", "nvidia.com/gpu": "2", "memory": "64Gi"}})
        oc = FakeOcRunner({"get nodes -o": _ok(data)})
        gpus = get_gpu_resources(oc)
        assert "worker-0" in gpus
        assert gpus["worker-0"]["nvidia.com/gpu"] == 2

    def test_no_gpu(self):
        data = _node_json({"name": "master-0", "allocatable": {"cpu": "8", "memory": "32Gi"}})
        oc = FakeOcRunner({"get nodes -o": _ok(data)})
        assert get_gpu_resources(oc) == {}

    def test_multiple_gpu_types(self):
        data = _node_json({"name": "worker-0", "allocatable": {"nvidia.com/gpu": "4", "amd.com/gpu": "1"}})
        oc = FakeOcRunner({"get nodes -o": _ok(data)})
        gpus = get_gpu_resources(oc)
        assert gpus["worker-0"]["nvidia.com/gpu"] == 4
        assert gpus["worker-0"]["amd.com/gpu"] == 1

    def test_vgpu_detected(self):
        data = _node_json({"name": "worker-0", "allocatable": {"nvidia.com/vgpu": "8"}})
        oc = FakeOcRunner({"get nodes -o": _ok(data)})
        gpus = get_gpu_resources(oc)
        assert gpus["worker-0"]["nvidia.com/vgpu"] == 8

    def test_no_false_positive_on_substring(self):
        data = _node_json({"name": "worker-0", "allocatable": {"hugepages-gpu-xyz": "1024"}})
        oc = FakeOcRunner({"get nodes -o": _ok(data)})
        assert get_gpu_resources(oc) == {}

    def test_api_unreachable(self):
        oc = FakeOcRunner({"get nodes -o": _fail()})
        assert get_gpu_resources(oc) == {}

    def test_bad_json(self):
        oc = FakeOcRunner({"get nodes -o": _ok("not json")})
        assert get_gpu_resources(oc) == {}


class TestPrintStatus:
    def _build_oc(self, version="4.16.3", nodes_json=None, csv_out=""):
        if nodes_json is None:
            nodes_json = _node_json({"name": "master-0", "roles": ["master"], "ready": True})
        return FakeOcRunner({
            "get clusterversion version": _ok(version),
            "get nodes -o": _ok(nodes_json),
            "get csv -A": _ok(csv_out),
        })

    def test_healthy_cluster(self, caplog):
        oc = self._build_oc(
            csv_out="ns   gpu-operator.v24.3   Succeeded\n",
        )
        with caplog.at_level("INFO", logger="accelerator_ci.cluster_provision.status"):
            rc = print_status(oc)
        assert rc == 0
        assert "4.16.3" in caplog.text
        assert "master-0" in caplog.text
        assert "gpu-operator" in caplog.text

    def test_api_unreachable(self):
        oc = FakeOcRunner({
            "get clusterversion version": _fail("connection refused"),
            "get nodes -o": _fail(),
            "get csv -A": _fail(),
        })
        rc = print_status(oc)
        assert rc == 1

    def test_no_operators(self, caplog):
        oc = self._build_oc()
        with caplog.at_level("INFO", logger="accelerator_ci.cluster_provision.status"):
            rc = print_status(oc)
        assert rc == 0
        assert "none found" in caplog.text

    def test_gpu_resources_shown(self, caplog):
        nodes = _node_json({"name": "gpu-worker", "roles": ["worker"], "allocatable": {"nvidia.com/gpu": "4"}})
        oc = self._build_oc(nodes_json=nodes)
        with caplog.at_level("INFO", logger="accelerator_ci.cluster_provision.status"):
            rc = print_status(oc)
        assert rc == 0
        assert "nvidia.com/gpu" in caplog.text

    def test_roles_shown(self, caplog):
        nodes = _node_json({"name": "combo", "roles": ["master", "worker"]})
        oc = self._build_oc(nodes_json=nodes)
        with caplog.at_level("INFO", logger="accelerator_ci.cluster_provision.status"):
            rc = print_status(oc)
        assert rc == 0
        assert "master,worker" in caplog.text
