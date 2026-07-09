from __future__ import annotations

import logging
from unittest.mock import MagicMock

import subprocess
import pytest

from accelerator_ci.operators.orchestrator import (
    install_operators,
    cleanup_operators,
    _validate_dependencies,
    _can_parallelize,
)
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
    "accelerator_ci.operators.orchestrator.is_operator_installed",
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
    result["is_operator_installed"].return_value = False
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

    def test_skips_already_installed_operator(self, mocks):
        oc = FakeOcRunner()
        mocks["is_operator_installed"].return_value = True

        install_operators(oc, FakeVendor(), {})

        mocks["ensure_namespace"].assert_not_called()
        mocks["create_subscription"].assert_not_called()
        mocks["wait_for_csv"].assert_not_called()

    def test_mixed_installed_and_new(self, mocks):
        class TwoOpVendor(FakeVendor):
            def get_operators(self, vendor_config):
                return [
                    OperatorSpec(
                        name="nfd", package="nfd-pkg",
                        namespace="nfd-ns", catalog="rh", channel="stable",
                    ),
                    OperatorSpec(
                        name="gpu", package="gpu-pkg",
                        namespace="gpu-ns", catalog="certified", channel="v24",
                    ),
                ]

        mocks["is_operator_installed"].side_effect = [True, False]

        oc = FakeOcRunner()
        install_operators(oc, TwoOpVendor(), {})

        mocks["ensure_namespace"].assert_called_once_with(oc, "gpu-ns")
        mocks["create_subscription"].assert_called_once()


class TestParallelInstall:
    def test_independent_operators_run_in_parallel(self, mocks):
        """Two operators with no deps — both should install."""
        class TwoIndependent(FakeVendor):
            def get_operators(self, vendor_config):
                return [
                    OperatorSpec(
                        name="nfd", package="nfd-pkg",
                        namespace="nfd-ns", catalog="rh", channel="stable",
                    ),
                    OperatorSpec(
                        name="monitoring", package="mon-pkg",
                        namespace="mon-ns", catalog="rh", channel="stable",
                    ),
                ]

        oc = FakeOcRunner()
        install_operators(oc, TwoIndependent(), {})

        assert mocks["ensure_namespace"].call_count == 2
        assert mocks["create_subscription"].call_count == 2

    def test_dependency_ordering(self, mocks):
        """GPU depends on NFD — both install, but GPU waits for NFD."""
        call_order = []
        mocks["ensure_namespace"].side_effect = lambda oc, ns: call_order.append(ns)

        class DepVendor(FakeVendor):
            def get_operators(self, vendor_config):
                return [
                    OperatorSpec(
                        name="NFD", package="nfd-pkg",
                        namespace="nfd-ns", catalog="rh", channel="stable",
                    ),
                    OperatorSpec(
                        name="GPU", package="gpu-pkg",
                        namespace="gpu-ns", catalog="cert", channel="v24",
                        depends_on=["nfd-pkg"],
                    ),
                ]

        oc = FakeOcRunner()
        install_operators(oc, DepVendor(), {})

        assert mocks["create_subscription"].call_count == 2
        assert call_order.index("nfd-ns") < call_order.index("gpu-ns")

    def test_diamond_dependency(self, mocks):
        """A depends on B and C, B and C are independent."""
        call_order = []
        mocks["ensure_namespace"].side_effect = lambda oc, ns: call_order.append(ns)

        class DiamondVendor(FakeVendor):
            def get_operators(self, vendor_config):
                return [
                    OperatorSpec(
                        name="B", package="b-pkg",
                        namespace="b-ns", catalog="rh", channel="stable",
                    ),
                    OperatorSpec(
                        name="C", package="c-pkg",
                        namespace="c-ns", catalog="rh", channel="stable",
                    ),
                    OperatorSpec(
                        name="A", package="a-pkg",
                        namespace="a-ns", catalog="rh", channel="stable",
                        depends_on=["b-pkg", "c-pkg"],
                    ),
                ]

        oc = FakeOcRunner()
        install_operators(oc, DiamondVendor(), {})

        assert mocks["create_subscription"].call_count == 3
        assert call_order.index("a-ns") > call_order.index("b-ns")
        assert call_order.index("a-ns") > call_order.index("c-ns")

    def test_skips_installed_in_parallel(self, mocks, caplog):
        """Already-installed operators are skipped even in parallel mode."""
        class TwoOps(FakeVendor):
            def get_operators(self, vendor_config):
                return [
                    OperatorSpec(
                        name="nfd", package="nfd-pkg",
                        namespace="nfd-ns", catalog="rh", channel="stable",
                    ),
                    OperatorSpec(
                        name="gpu", package="gpu-pkg",
                        namespace="gpu-ns", catalog="cert", channel="v24",
                    ),
                ]

        mocks["is_operator_installed"].return_value = True

        oc = FakeOcRunner()
        with caplog.at_level(logging.INFO):
            install_operators(oc, TwoOps(), {})

        mocks["ensure_namespace"].assert_not_called()
        assert "nfd already installed" in caplog.text
        assert "gpu already installed" in caplog.text

    def test_single_operator_stays_sequential(self, mocks):
        oc = FakeOcRunner()
        install_operators(oc, FakeVendor(), {})

        mocks["ensure_namespace"].assert_called_once()
        mocks["create_subscription"].assert_called_once()

    def test_dependency_failure_stops_dependents(self, mocks):
        """If NFD fails, GPU (which depends on NFD) should not install."""
        mocks["ensure_namespace"].side_effect = RuntimeError("namespace creation failed")

        class DepVendor(FakeVendor):
            def get_operators(self, vendor_config):
                return [
                    OperatorSpec(
                        name="NFD", package="nfd-pkg",
                        namespace="nfd-ns", catalog="rh", channel="stable",
                    ),
                    OperatorSpec(
                        name="GPU", package="gpu-pkg",
                        namespace="gpu-ns", catalog="cert", channel="v24",
                        depends_on=["nfd-pkg"],
                    ),
                ]

        oc = FakeOcRunner()
        with pytest.raises(RuntimeError, match="Operator installation failed"):
            install_operators(oc, DepVendor(), {})


class TestValidateDependencies:
    def test_valid_deps(self):
        ops = [
            OperatorSpec(name="A", package="a", namespace="ns", catalog="c", channel="s"),
            OperatorSpec(name="B", package="b", namespace="ns", catalog="c", channel="s",
                         depends_on=["a"]),
        ]
        _validate_dependencies(ops)

    def test_unknown_dependency(self):
        ops = [
            OperatorSpec(name="A", package="a", namespace="ns", catalog="c", channel="s",
                         depends_on=["nonexistent"]),
        ]
        with pytest.raises(ValueError, match="unknown package 'nonexistent'"):
            _validate_dependencies(ops)

    def test_cycle_detection(self):
        ops = [
            OperatorSpec(name="A", package="a", namespace="ns", catalog="c", channel="s",
                         depends_on=["b"]),
            OperatorSpec(name="B", package="b", namespace="ns", catalog="c", channel="s",
                         depends_on=["a"]),
        ]
        with pytest.raises(ValueError, match="cycle"):
            _validate_dependencies(ops)

    def test_self_cycle(self):
        ops = [
            OperatorSpec(name="A", package="a", namespace="ns", catalog="c", channel="s",
                         depends_on=["a"]),
        ]
        with pytest.raises(ValueError, match="cycle"):
            _validate_dependencies(ops)

    def test_no_deps(self):
        ops = [
            OperatorSpec(name="A", package="a", namespace="ns", catalog="c", channel="s"),
            OperatorSpec(name="B", package="b", namespace="ns", catalog="c", channel="s"),
        ]
        _validate_dependencies(ops)


class TestCanParallelize:
    def test_single_operator(self):
        ops = [OperatorSpec(name="A", package="a", namespace="ns", catalog="c", channel="s")]
        assert _can_parallelize(ops) is False

    def test_empty(self):
        assert _can_parallelize([]) is False

    def test_two_operators(self):
        ops = [
            OperatorSpec(name="A", package="a", namespace="ns", catalog="c", channel="s"),
            OperatorSpec(name="B", package="b", namespace="ns", catalog="c", channel="s"),
        ]
        assert _can_parallelize(ops) is True


class TestCleanupOperators:
    def test_calls_vendor_cleanup(self):
        oc = FakeOcRunner()
        vendor = FakeVendor()
        vendor.cleanup = MagicMock()
        cleanup_operators(oc, vendor)
        vendor.cleanup.assert_called_once_with(oc)
