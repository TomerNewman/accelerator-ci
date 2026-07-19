"""Example vendor profile — installs NFD, NMState, and MetalLB.

Demonstrates parallel operator installation with dependency ordering.
Works on any OpenShift cluster without special hardware.

Usage:
    accelerator-ci --config config.yaml --vendor-module examples.example_vendor.profile operators
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from accelerator_ci.vendors.base import VendorProfile, OperatorSpec
from accelerator_ci.shared.oc_runner import OcRunner

logger = logging.getLogger(__name__)

NODE_LABEL = "example-vendor.io/enabled"


class Profile(VendorProfile):
    @property
    def display_name(self) -> str:
        return "Example Vendor"

    def get_operators(self, vendor_config: dict[str, Any]) -> list[OperatorSpec]:
        return [
            OperatorSpec(
                name="nfd",
                package="nfd",
                namespace="openshift-nfd",
                catalog="redhat-operators",
                channel=vendor_config.get("nfd_channel", "stable"),
            ),
            OperatorSpec(
                name="nmstate",
                package="kubernetes-nmstate-operator",
                namespace="openshift-nmstate",
                catalog="redhat-operators",
                channel=vendor_config.get("nmstate_channel", "stable"),
            ),
            OperatorSpec(
                name="metallb",
                package="metallb-operator",
                namespace="metallb-system",
                catalog="redhat-operators",
                channel=vendor_config.get("metallb_channel", "stable"),
                all_namespaces=True,
                depends_on=["nfd", "kubernetes-nmstate-operator"],
            ),
        ]

    def get_base_operators(self, vendor_config: dict[str, Any]) -> list[OperatorSpec]:
        """NFD and NMState are cached in the snapshot; MetalLB installs fresh."""
        return [
            OperatorSpec(
                name="nfd",
                package="nfd",
                namespace="openshift-nfd",
                catalog="redhat-operators",
                channel=vendor_config.get("nfd_channel", "stable"),
            ),
            OperatorSpec(
                name="nmstate",
                package="kubernetes-nmstate-operator",
                namespace="openshift-nmstate",
                catalog="redhat-operators",
                channel=vendor_config.get("nmstate_channel", "stable"),
            ),
        ]

    def pre_operator_setup(
        self, oc: OcRunner, vendor_config: dict[str, Any], machine_config_role: str,
    ) -> bool:
        logger.info("Labeling %s nodes with %s=true", machine_config_role, NODE_LABEL)
        oc.oc(
            "label", "nodes",
            f"--selector=node-role.kubernetes.io/{machine_config_role}=",
            f"{NODE_LABEL}=true", "--overwrite",
        )
        return False
    def post_operator_setup(
        self, oc: OcRunner, vendor_config: dict[str, Any], ocp_version: str | None,
    ) -> None:
        nfd_cr = {
            "apiVersion": "nfd.openshift.io/v1",
            "kind": "NodeFeatureDiscovery",
            "metadata": {"name": "nfd-instance", "namespace": "openshift-nfd"},
            "spec": {},
        }
        logger.info("Creating NodeFeatureDiscovery CR")
        oc.apply_yaml(json.dumps(nfd_cr))

        nmstate_cr = {
            "apiVersion": "nmstate.io/v1",
            "kind": "NMState",
            "metadata": {"name": "nmstate"},
        }
        logger.info("Creating NMState CR")
        oc.apply_yaml(json.dumps(nmstate_cr))

    def wait_for_gpu_ready(self, oc: OcRunner, timeout: int = 900) -> None:
        """Wait until NFD has labeled at least one node with CPU feature labels."""
        logger.info("Waiting for NFD labels on nodes (timeout: %ds)", timeout)
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            r = oc.oc("get", "nodes", "-o", "json", timeout=15)
            if r.returncode == 0 and r.stdout:
                nodes = json.loads(r.stdout).get("items", [])
                for node in nodes:
                    labels = (node.get("metadata") or {}).get("labels", {})
                    nfd_labels = [k for k in labels if k.startswith("feature.node.kubernetes.io/")]
                    if nfd_labels:
                        name = node["metadata"]["name"]
                        logger.info("NFD labels found on node %s (%d labels)", name, len(nfd_labels))
                        return
            time.sleep(15)
        raise RuntimeError(f"Timeout ({timeout}s) waiting for NFD labels on nodes")

    def cleanup(self, oc: OcRunner) -> None:
        logger.info("Deleting NodeFeatureDiscovery CR")
        oc.oc("delete", "NodeFeatureDiscovery", "nfd-instance",
               "-n", "openshift-nfd", "--ignore-not-found")

        logger.info("Deleting NMState CR")
        oc.oc("delete", "NMState", "nmstate", "--ignore-not-found")

        for ns in ("metallb-system", "openshift-nmstate", "openshift-nfd"):
            logger.info("Deleting subscription and CSV in %s", ns)
            oc.oc("delete", "subscription", "--all", "-n", ns, "--ignore-not-found")
            oc.oc("delete", "csv", "--all", "-n", ns, "--ignore-not-found")

        logger.info("Removing node label %s", NODE_LABEL)
        oc.oc("label", "nodes", "--all", f"{NODE_LABEL}-")

    def get_test_path(self) -> str:
        return "examples/example_vendor/tests"
