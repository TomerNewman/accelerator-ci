"""GPU operator installation orchestrator."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from accelerator_ci.operators.cluster_health import wait_for_cluster_stability, wait_for_mcp_updated
from accelerator_ci.operators.install import (
    ensure_namespace,
    create_operator_group,
    create_subscription,
    approve_install_plan,
    wait_for_csv,
    wait_for_csv_by_name,
)
from accelerator_ci.operators.prerequisites import configure_internal_registry, verify_required_operators

if TYPE_CHECKING:
    from accelerator_ci.shared.oc_runner import OcRunner
    from accelerator_ci.vendors.base import VendorProfile

logger = logging.getLogger(__name__)


DEFAULT_TIMEOUTS = {
    "prerequisite": 900,
    "registry": 120,
    "operator": 600,
    "cluster_stability": 900,
    "gpu_ready": 1800,
}


def install_operators(
    oc: OcRunner,
    vendor: VendorProfile,
    vendor_config: dict[str, Any],
    machine_config_role: str = "worker",
    ocp_version: str | None = None,
    timeouts: dict[str, int] | None = None,
) -> None:
    """Use machine_config_role="master" for SNO."""
    t = {**DEFAULT_TIMEOUTS, **(timeouts or {})}

    logger.info("%s\n%s Installation (OLM)\n%s", "=" * 60, vendor.display_name, "=" * 60)

    verify_required_operators(oc, timeout=t["prerequisite"])
    configure_internal_registry(oc, timeout=t["registry"])
    wait_for_cluster_stability(oc, timeout=t["cluster_stability"])

    vendor.pre_operator_setup(oc, vendor_config, machine_config_role)
    wait_for_mcp_updated(oc)
    wait_for_cluster_stability(oc, timeout=t["cluster_stability"])

    ops = vendor.get_operators(vendor_config)
    for op in ops:
        logger.info("Installing operator: %s in %s...", op.name, op.namespace)
        ensure_namespace(oc, op.namespace)
        create_operator_group(oc, op.namespace, op.name, all_namespaces=op.all_namespaces)
        create_subscription(
            oc,
            op.namespace,
            op.name,
            op.package,
            op.catalog,
            op.channel,
            starting_csv=op.starting_csv,
            manual_approval=op.manual_approval,
        )
        if op.manual_approval and op.starting_csv:
            approve_install_plan(oc, op.namespace, op.starting_csv, timeout=t["operator"])
            wait_for_csv_by_name(oc, op.namespace, op.starting_csv, timeout=t["operator"])
        else:
            wait_for_csv(oc, op.namespace, timeout=t["operator"])
        logger.info("%s installed.", op.name)

    vendor.post_operator_setup(oc, vendor_config, ocp_version)
    wait_for_cluster_stability(oc, timeout=t["cluster_stability"])
    vendor.wait_for_gpu_ready(oc, timeout=t["gpu_ready"])

    logger.info("%s\n%s installation completed successfully.\n%s", "=" * 60, vendor.display_name, "=" * 60)


def cleanup_operators(
    oc: OcRunner,
    vendor: VendorProfile,
) -> None:
    logger.info("%s\n%s Cleanup\n%s", "=" * 60, vendor.display_name, "=" * 60)

    vendor.cleanup(oc)

    logger.info("%s\n%s cleanup completed.\n%s", "=" * 60, vendor.display_name, "=" * 60)
