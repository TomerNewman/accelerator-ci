from __future__ import annotations

from unittest.mock import MagicMock

import subprocess
import pytest

from accelerator_ci.operators.orchestrator import install_operators, cleanup_operators
from accelerator_ci.vendors.base import OperatorSpec, VendorProfile


class FakeOcRunner:
    def oc(self, *args, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    def apply_yaml(self, yaml_content: str, timeout: int = 120) -> None:
        pass


class FakeVendor(VendorProfile):
    @property
    def display_name(self) -> str:
        return "Test GPU"

    def get_operators(self, vendor_config):
        return [
            OperatorSpec(
                name="gpu-operator", package="gpu-pkg",
                namespace="gpu-ns", catalog="certified", channel="stable",
            ),
        ]

    def post_operator_setup(self, oc, vendor_config, ocp_version):
        pass

    def wait_for_gpu_ready(self, oc, timeout=900):
        pass


class FakeVendorManual(FakeVendor):
    def get_operators(self, vendor_config):
        return [
            OperatorSpec(
                name="manual-op", package="manual-pkg",
                namespace="manual-ns", catalog="catalog", channel="alpha",
                starting_csv="manual-pkg.v1.0", manual_approval=True,
            ),
        ]


PATCHED_DEPS = [
    "accelerator_ci.operators.orchestrator.wait_for_cluster_stability",
    "accelerator_ci.operators.orchestrator.wait_for_mcp_updated",
    "accelerator_ci.operators.orchestrator.configure_internal_registry",
    "accelerator_ci.operators.orchestrator.verify_required_operators",
    "accelerator_ci.operators.orchestrator.ensure_namespace",
    "accelerator_ci.operators.orchestrator.create_operator_group",
    "accelerator_ci.operators.orchestrator.create_subscription",
    "accelerator_ci.operators.orchestrator.wait_for_csv",
    "accelerator_ci.operators.orchestrator.wait_for_csv_by_name",
    "accelerator_ci.operators.orchestrator.approve_install_plan",
]


@pytest.fixture
def mocks(monkeypatch):
    result = {}
    for target in PATCHED_DEPS:
        m = MagicMock()
        monkeypatch.setattr(target, m)
        short_name = target.rsplit(".", 1)[1]
        result[short_name] = m
    return result


class TestInstallOperators:
    def test_automatic_approval_flow(self, mocks):
        oc = FakeOcRunner()
        install_operators(oc, FakeVendor(), {})

        mocks["ensure_namespace"].assert_called_once_with(oc, "gpu-ns")
        mocks["create_operator_group"].assert_called_once_with(
            oc, "gpu-ns", "gpu-operator", all_namespaces=False,
        )
        mocks["create_subscription"].assert_called_once()
        mocks["wait_for_csv"].assert_called_once_with(oc, "gpu-ns", timeout=600)
        mocks["approve_install_plan"].assert_not_called()

    def test_manual_approval_flow(self, mocks):
        oc = FakeOcRunner()
        install_operators(oc, FakeVendorManual(), {})

        mocks["approve_install_plan"].assert_called_once_with(
            oc, "manual-ns", "manual-pkg.v1.0", timeout=600,
        )
        mocks["wait_for_csv_by_name"].assert_called_once_with(
            oc, "manual-ns", "manual-pkg.v1.0", timeout=600,
        )
        mocks["wait_for_csv"].assert_not_called()

    def test_custom_timeouts(self, mocks):
        oc = FakeOcRunner()
        install_operators(oc, FakeVendor(), {}, timeouts={"operator": 999})
        mocks["wait_for_csv"].assert_called_once_with(oc, "gpu-ns", timeout=999)

    def test_vendor_hooks_called(self, mocks):
        oc = FakeOcRunner()
        vendor = FakeVendor()
        vendor.pre_operator_setup = MagicMock()
        vendor.post_operator_setup = MagicMock()
        vendor.wait_for_gpu_ready = MagicMock()

        install_operators(oc, vendor, {"key": "val"}, machine_config_role="master")

        vendor.pre_operator_setup.assert_called_once_with(oc, {"key": "val"}, "master")
        vendor.post_operator_setup.assert_called_once()
        vendor.wait_for_gpu_ready.assert_called_once()


class TestCleanupOperators:
    def test_calls_vendor_cleanup(self):
        oc = FakeOcRunner()
        vendor = FakeVendor()
        vendor.cleanup = MagicMock()
        cleanup_operators(oc, vendor)
        vendor.cleanup.assert_called_once_with(oc)
