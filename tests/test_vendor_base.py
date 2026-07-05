from __future__ import annotations

import pytest
from typing import Any

from accelerator_ci.vendors.base import VendorProfile, OperatorSpec


class FakeVendor(VendorProfile):
    @property
    def display_name(self) -> str:
        return "Fake GPU"

    def get_operators(self, vendor_config: dict[str, Any]) -> list[OperatorSpec]:
        return [
            OperatorSpec(
                name="fake-operator",
                package="fake-operator",
                namespace="fake-ns",
                catalog="community-operators",
                channel="stable",
            ),
        ]

    def pre_operator_setup(self, oc, vendor_config, machine_config_role):
        pass

    def post_operator_setup(self, oc, vendor_config, ocp_version):
        pass

    def wait_for_gpu_ready(self, oc, timeout=900):
        pass

    def cleanup(self, oc):
        pass

    def get_test_path(self) -> str:
        return "fake/tests"


class TestOperatorSpec:
    def test_defaults(self):
        spec = OperatorSpec(
            name="op", package="pkg", namespace="ns",
            catalog="cat", channel="stable",
        )
        assert spec.starting_csv is None
        assert spec.manual_approval is False
        assert spec.all_namespaces is False

    def test_all_fields(self):
        spec = OperatorSpec(
            name="op", package="pkg", namespace="ns",
            catalog="cat", channel="alpha",
            starting_csv="op.v1.0.0",
            manual_approval=True,
            all_namespaces=True,
        )
        assert spec.starting_csv == "op.v1.0.0"
        assert spec.manual_approval is True
        assert spec.all_namespaces is True


class TestVendorProfile:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            VendorProfile()

    def test_concrete_implementation(self):
        vendor = FakeVendor()
        assert vendor.display_name == "Fake GPU"
        assert vendor.get_test_path() == "fake/tests"

    def test_get_operators(self):
        vendor = FakeVendor()
        ops = vendor.get_operators({})
        assert len(ops) == 1
        assert ops[0].name == "fake-operator"
        assert ops[0].namespace == "fake-ns"

    def test_host_setup_default_noop(self):
        vendor = FakeVendor()
        assert vendor.host_setup("host", "root", None, {}) is None

    def test_get_pci_devices_default_empty(self):
        vendor = FakeVendor()
        assert vendor.get_pci_devices("host", "root", None, {}) == []

    def test_resolve_operator_version_default(self):
        vendor = FakeVendor()
        assert vendor.resolve_operator_version("1.4") == "1.4"

    def test_incomplete_profile_raises(self):
        class Incomplete(VendorProfile):
            @property
            def display_name(self):
                return "Incomplete"

        with pytest.raises(TypeError):
            Incomplete()
