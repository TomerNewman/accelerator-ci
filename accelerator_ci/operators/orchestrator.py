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
    is_operator_installed,
    wait_for_csv,
    wait_for_csv_by_name,
)
from accelerator_ci.operators.prerequisites import configure_internal_registry, verify_required_operators
from accelerator_ci.shared.progress import ProgressTracker

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
    json_progress: bool = False,
) -> None:
    """Use machine_config_role="master" for SNO."""
    t = {**DEFAULT_TIMEOUTS, **(timeouts or {})}

    ops = vendor.get_operators(vendor_config)

    step_names = [
        "Verify prerequisites",
        "Configure internal registry",
        "Wait for cluster stability",
        "Vendor pre-operator setup",
        "Wait for MachineConfigPool",
        "Wait for cluster stability",
    ]
    for op in ops:
        step_names.append(f"Install {op.name}")
    step_names += [
        "Vendor post-operator setup",
        "Wait for cluster stability",
        "Wait for GPU readiness",
    ]

    progress = ProgressTracker(
        f"{vendor.display_name} operators", step_names, json_output=json_progress,
    )
    progress.start()

    try:
        step = 1
        progress.step(step)
        verify_required_operators(oc, timeout=t["prerequisite"])

        step += 1
        progress.step(step)
        configure_internal_registry(oc, timeout=t["registry"])

        step += 1
        progress.step(step)
        wait_for_cluster_stability(oc, timeout=t["cluster_stability"])

        step += 1
        progress.step(step)
        vendor.pre_operator_setup(oc, vendor_config, machine_config_role)

        step += 1
        progress.step(step)
        wait_for_mcp_updated(oc)

        step += 1
        progress.step(step)
        wait_for_cluster_stability(oc, timeout=t["cluster_stability"])

        for op in ops:
            step += 1
            progress.step(step)

            if is_operator_installed(oc, op.namespace, op.package):
                logger.info("%s already installed and healthy — skipping", op.name)
                continue

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

        step += 1
        progress.step(step)
        vendor.post_operator_setup(oc, vendor_config, ocp_version)

        step += 1
        progress.step(step)
        wait_for_cluster_stability(oc, timeout=t["cluster_stability"])

        step += 1
        progress.step(step)
        vendor.wait_for_gpu_ready(oc, timeout=t["gpu_ready"])

        progress.done()

    except Exception as exc:
        progress.fail(str(exc))
        raise


def cleanup_operators(
    oc: OcRunner,
    vendor: VendorProfile,
) -> None:
    logger.info("%s\n%s Cleanup\n%s", "=" * 60, vendor.display_name, "=" * 60)

    vendor.cleanup(oc)

    logger.info("%s\n%s cleanup completed.\n%s", "=" * 60, vendor.display_name, "=" * 60)
