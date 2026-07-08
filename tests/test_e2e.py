"""End-to-end integration tests.

Level 1: CLI integration — runs the real CLI binary with --dry-run against
         actual config files. No mocks, no cluster, just wiring verification.

Level 2: Mock cluster — runs full workflows (status, operators) against a
         FakeOcRunner that returns realistic oc output. Verifies the
         orchestration glues together correctly without real infrastructure.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from typing import Any
import pytest
import yaml

from accelerator_ci.cluster_provision.main import main
from accelerator_ci.cluster_provision.status import print_status, get_node_status
from accelerator_ci.operators.orchestrator import install_operators, cleanup_operators
from accelerator_ci.shared.oc_runner import OcRunner, LocalOcRunner
from accelerator_ci.vendors.base import OperatorSpec, VendorProfile


_SNO_CONFIG = {
    "cluster_name": "e2e-sno",
    "ocp_version": "4.20",
    "pull_secret_path": "/tmp/pull-secret.json",
    "domain": "lab.local",
    "ctlplanes": 1,
    "workers": 0,
    "ctlplane": {"numcpus": 16, "memory": 65536},
    "worker": {"numcpus": 8, "memory": 32768},
    "disk_size": 200,
    "network": "lab-net",
    "api_ip": "10.0.0.100",
    "remote": {"host": "gpu-box.lab", "user": "admin", "ssh_key_path": "/tmp/id_rsa"},
    "pci_devices": ["0000:41:00.0"],
    "wait_timeout": 7200,
    "version_channel": "stable",
    "vendor": "acme-gpu",
    "operators": {
        "machine_config_role": "worker",
        "driver_version": "550.127",
        "gpu_operator_version": "24.3.0",
    },
    "must_gather": {"artifact_dir": "/tmp/e2e-artifacts"},
}

_MULTI_NODE_CONFIG = {
    **_SNO_CONFIG,
    "cluster_name": "e2e-multi",
    "ctlplanes": 3,
    "workers": 2,
    "remote": {"host": None, "user": "root", "ssh_key_path": None},
    "pci_devices": [],
}

_BYOC_CONFIG = {
    "cluster_name": "e2e-byoc",
    "ocp_version": "4.18",
}


@pytest.fixture
def sno_config(tmp_path):
    p = tmp_path / "sno.yaml"
    p.write_text(yaml.dump(_SNO_CONFIG))
    return str(p)


@pytest.fixture
def multi_config(tmp_path):
    p = tmp_path / "multi.yaml"
    p.write_text(yaml.dump(_MULTI_NODE_CONFIG))
    return str(p)


@pytest.fixture
def byoc_config(tmp_path):
    p = tmp_path / "byoc.yaml"
    p.write_text(yaml.dump(_BYOC_CONFIG))
    return str(p)


@pytest.fixture
def kubeconfig(tmp_path):
    kc = tmp_path / "kubeconfig"
    kc.write_text("apiVersion: v1\nkind: Config")
    return str(kc)


class TestCLIDryRunSNO:
    """Full --dry-run through a realistic SNO + remote config."""

    def test_deploy(self, sno_config, capsys):
        rc = main(["--config", sno_config, "--dry-run", "deploy"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "e2e-sno" in out
        assert "remote" in out
        assert "gpu-box.lab" in out
        assert "SNO" in out
        assert "PCI devices" in out
        assert "0000:41:00.0" in out

    def test_delete(self, sno_config, capsys):
        rc = main(["--config", sno_config, "--dry-run", "delete"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "e2e-sno" in out
        assert "remote" in out

    def test_must_gather(self, sno_config, capsys):
        rc = main(["--config", sno_config, "--dry-run", "must-gather"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "/tmp/e2e-artifacts" in out
        assert "SCP" in out

    def test_status(self, sno_config, capsys):
        rc = main(["--config", sno_config, "--dry-run", "status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "clusterversion" in out
        assert "nodes" in out

    def test_operators_needs_vendor(self, sno_config, capsys):
        rc = main(["--config", sno_config, "--dry-run", "operators"])
        assert rc == 1
        assert "vendor-module" in capsys.readouterr().out.lower()


class TestCLIDryRunMultiNode:
    """Full --dry-run through a multi-node local config."""

    def test_deploy(self, multi_config, capsys):
        rc = main(["--config", multi_config, "--dry-run", "deploy"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "e2e-multi" in out
        assert "local" in out
        assert "3 control-plane" in out
        assert "2 worker(s)" in out

    def test_delete(self, multi_config, capsys):
        rc = main(["--config", multi_config, "--dry-run", "delete"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "e2e-multi" in out
        assert "local" in out

    def test_must_gather(self, multi_config, capsys):
        rc = main(["--config", multi_config, "--dry-run", "must-gather"])
        assert rc == 0
        assert "must-gather.sh locally" in capsys.readouterr().out


class TestCLIDryRunBYOC:
    """Full --dry-run for bring-your-own-cluster configs."""

    def test_deploy_with_kubeconfig(self, byoc_config, kubeconfig, capsys):
        rc = main(["--config", byoc_config, "--dry-run", "--kubeconfig", kubeconfig, "deploy"])
        assert rc == 0
        assert "SKIPPED" in capsys.readouterr().out

    def test_delete_with_kubeconfig(self, byoc_config, kubeconfig, capsys):
        rc = main(["--config", byoc_config, "--dry-run", "--kubeconfig", kubeconfig, "delete"])
        assert rc == 0
        assert "SKIPPED" in capsys.readouterr().out

    def test_deploy_without_kubeconfig(self, byoc_config, capsys):
        rc = main(["--config", byoc_config, "--dry-run", "deploy"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "e2e-byoc" in out


class TestCLIConfigErrors:
    """CLI handles bad configs gracefully."""

    def test_missing_config_file(self, capsys):
        rc = main(["--config", "/nonexistent/config.yaml", "deploy"])
        assert rc == 1
        assert "not found" in capsys.readouterr().out.lower()

    def test_empty_config_file(self, tmp_path, capsys):
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")
        rc = main(["--config", str(cfg), "deploy"])
        assert rc == 1

    def test_invalid_types(self, tmp_path, capsys):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(yaml.dump({
            "cluster_name": "test",
            "ocp_version": "4.20",
            "ctlplanes": "not_an_int",
        }))
        rc = main(["--config", str(cfg), "--dry-run", "deploy"])
        assert rc == 1
        assert "ctlplanes" in capsys.readouterr().out

    def test_no_command(self, tmp_path, capsys):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"cluster_name": "t", "ocp_version": "4.20"}))
        rc = main(["--config", str(cfg)])
        assert rc == 1


class FakeOcRunner(OcRunner):
    """Returns realistic oc output for e2e workflow tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._nodes_json = json.dumps({"items": [
            {
                "metadata": {
                    "name": "worker-0.lab.local",
                    "labels": {
                        "node-role.kubernetes.io/worker": "",
                        "node-role.kubernetes.io/master": "",
                    },
                },
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {"kubeletVersion": "v1.33.1+abc"},
                    "allocatable": {
                        "cpu": "16",
                        "memory": "65536Mi",
                        "acme.com/gpu": "2",
                    },
                },
            },
        ]})
        self._csvs_json = json.dumps({"items": [
            {
                "metadata": {"name": "gpu-operator.v24.3.0", "namespace": "gpu-operator"},
                "status": {"phase": "Succeeded"},
            },
            {
                "metadata": {"name": "nfd.v4.18.0", "namespace": "openshift-nfd"},
                "status": {"phase": "Succeeded"},
            },
        ]})
        self._mcp_json = json.dumps({"items": [
            {"metadata": {"name": "worker"}, "status": {
                "conditions": [
                    {"type": "Updated", "status": "True"},
                    {"type": "Updating", "status": "False"},
                    {"type": "Degraded", "status": "False"},
                ],
                "readyMachineCount": 1,
                "machineCount": 1,
            }},
        ]})

    def _ok(self, args, stdout="") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def oc(self, *args: str, timeout=None, stdin=None, retries=3) -> subprocess.CompletedProcess:
        self.calls.append(args)
        args_str = " ".join(args)

        if args[:3] == ("get", "clusterversion", "version"):
            return self._ok(args, "4.20.5")

        # Pods listing (verify_required_operators and configure_internal_registry)
        if args[:2] == ("get", "pods"):
            if "-n" in args:
                return self._ok(args, "image-registry-abc-xyz   1/1   Running   0   5d")
            return self._ok(args, textwrap.dedent("""\
                openshift-service-ca     service-ca-abc-xyz              1/1   Running   0   5d
                openshift-operator-lifecycle-manager  operator-lifecycle-abc   1/1   Running   0   5d
                openshift-machine-config-operator     machine-config-abc      1/1   Running   0   5d
                openshift-image-registry              image-registry-abc      1/1   Running   0   5d
            """))

        # Nodes — JSON (status, get_gpu_resources)
        if args[:2] == ("get", "nodes") and "json" in args_str:
            return self._ok(args, self._nodes_json)

        # Nodes — custom-columns (wait_for_cluster_stability)
        if args[:2] == ("get", "nodes"):
            return self._ok(args, "worker-0.lab.local   True")

        # ClusterOperators (wait_for_cluster_stability)
        if args[:2] == ("get", "clusteroperators"):
            return self._ok(args, textwrap.dedent("""\
                authentication   True   False   False
                console          True   False   False
                etcd             True   False   False
                network          True   False   False
            """))

        # MCP (wait_for_mcp_updated)
        if args[:2] == ("get", "mcp"):
            return self._ok(args, "worker   True   False   False")

        # CSVs — jsonpath (wait_for_csv)
        if args[:2] == ("get", "csv") and "jsonpath" in args_str and "phase" in args_str:
            return self._ok(args, "Succeeded")

        # CSVs — JSON (is_operator_installed)
        if args[:2] == ("get", "csv") and "json" in args_str:
            return self._ok(args, self._csvs_json)

        # CSVs — custom-columns (status report)
        if args[:2] == ("get", "csv"):
            return self._ok(args, textwrap.dedent("""\
                gpu-operator   gpu-operator.v24.3.0   Succeeded
                openshift-nfd  nfd.v4.18.0            Succeeded
            """))

        # Namespace/OperatorGroup/Subscription lookups
        if args[:2] in (("get", "namespace"), ("get", "operatorgroup"),
                         ("get", "subscription"), ("get", "installplan")):
            return self._ok(args)

        if args[0] in ("create", "patch", "apply", "label", "delete"):
            return self._ok(args, f"{args[0]}d")

        return self._ok(args)

    def apply_yaml(self, yaml_content: str, timeout: int = 120) -> None:
        self.calls.append(("apply_yaml",))


class FakeVendor(VendorProfile):
    """Minimal vendor for e2e tests."""

    @property
    def display_name(self) -> str:
        return "FakeGPU"

    def get_operators(self, vendor_config: dict[str, Any]) -> list[OperatorSpec]:
        return [
            OperatorSpec(
                name="NFD",
                package="nfd",
                namespace="openshift-nfd",
                catalog="redhat-operators",
                channel="stable",
            ),
            OperatorSpec(
                name="GPU Operator",
                package="gpu-operator",
                namespace="gpu-operator",
                catalog="certified-operators",
                channel="v24.3",
            ),
        ]

    def post_operator_setup(self, oc, vendor_config, ocp_version):
        pass

    def wait_for_gpu_ready(self, oc, timeout=900):
        pass


class TestStatusE2E:
    """Level 2: print_status against FakeOcRunner."""

    def test_full_status_report(self, caplog):
        oc = FakeOcRunner()
        with caplog.at_level("INFO"):
            rc = print_status(oc)
        assert rc == 0
        out = caplog.text
        assert "4.20.5" in out
        assert "worker-0.lab.local" in out
        assert "Ready" in out
        assert "gpu-operator.v24.3.0" in out
        assert "Succeeded" in out
        assert "acme.com/gpu" in out
        assert "2" in out



class TestOperatorInstallE2E:
    """Level 2: install_operators against FakeOcRunner + FakeVendor."""

    @pytest.fixture
    def _fast_time(self, monkeypatch):
        """Skip real waits: sleep is no-op, monotonic advances 61s per call
        to clear the MCP 60s guard on the second poll."""
        counter = [0.0]
        def fake_monotonic():
            counter[0] += 61.0
            return counter[0]
        monkeypatch.setattr("time.sleep", lambda _: None)
        monkeypatch.setattr("time.monotonic", fake_monotonic)

    _FAST_TIMEOUTS = {
        "prerequisite": 500, "registry": 500, "operator": 500,
        "cluster_stability": 500, "gpu_ready": 500,
    }

    def test_installs_both_operators(self, _fast_time):
        oc = FakeOcRunner()
        oc._csvs_json = json.dumps({"items": []})
        vendor = FakeVendor()
        install_operators(
            oc, vendor=vendor, vendor_config={},
            machine_config_role="worker", ocp_version="4.20",
            timeouts=self._FAST_TIMEOUTS,
        )
        apply_yaml_calls = [c for c in oc.calls if c == ("apply_yaml",)]
        assert len(apply_yaml_calls) >= 2

    def test_skips_installed_operator(self, _fast_time, caplog):
        oc = FakeOcRunner()
        oc._csvs_json = json.dumps({"items": [
            {
                "metadata": {"name": "nfd.v4.18.0", "namespace": "openshift-nfd"},
                "status": {"phase": "Succeeded"},
            },
        ]})
        vendor = FakeVendor()
        with caplog.at_level("INFO"):
            install_operators(
                oc, vendor=vendor, vendor_config={},
                machine_config_role="worker", ocp_version="4.20",
                timeouts=self._FAST_TIMEOUTS,
            )
        assert "NFD already installed" in caplog.text

    def test_progress_json(self, _fast_time, capsys):
        oc = FakeOcRunner()
        vendor = FakeVendor()
        install_operators(
            oc, vendor=vendor, vendor_config={},
            machine_config_role="worker", ocp_version="4.20",
            json_progress=True, timeouts=self._FAST_TIMEOUTS,
        )
        out = capsys.readouterr().out
        events = [json.loads(line) for line in out.strip().splitlines() if line.strip()]
        event_types = [e["event"] for e in events]
        assert "workflow_start" in event_types
        assert "workflow_done" in event_types
        assert event_types.count("step_start") >= 5


class TestCleanupE2E:
    """Level 2: cleanup_operators against FakeOcRunner + FakeVendor."""

    def test_cleanup_completes(self, caplog):
        oc = FakeOcRunner()
        vendor = FakeVendor()
        with caplog.at_level("INFO"):
            cleanup_operators(oc, vendor=vendor)
        assert "FakeGPU" in caplog.text
        assert "cleanup completed" in caplog.text


@pytest.mark.skipif(
    "ACCELERATOR_CI_E2E_KUBECONFIG" not in os.environ,
    reason="Set ACCELERATOR_CI_E2E_KUBECONFIG to run real cluster tests",
)
class TestRealClusterE2E:
    """Run with: ACCELERATOR_CI_E2E_KUBECONFIG=/path/to/kc pytest tests/test_e2e.py -k real"""

    @pytest.fixture
    def oc(self):
        return LocalOcRunner(os.environ["ACCELERATOR_CI_E2E_KUBECONFIG"])

    def test_cluster_reachable(self, oc):
        r = oc.oc("version", timeout=10, retries=0)
        assert r.returncode == 0

    def test_status_succeeds(self, oc):
        rc = print_status(oc)
        assert rc == 0

    def test_nodes_are_ready(self, oc):
        nodes = get_node_status(oc)
        assert len(nodes) > 0
        for node in nodes:
            assert node["ready"], f"{node['name']} is not Ready"
