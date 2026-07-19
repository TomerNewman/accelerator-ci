"""GPU operator installation orchestrator."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    from accelerator_ci.vendors.base import OperatorSpec, VendorProfile

logger = logging.getLogger(__name__)


DEFAULT_TIMEOUTS = {
    "prerequisite": 900,
    "registry": 120,
    "operator": 600,
    "cluster_stability": 900,
    "gpu_ready": 1800,
}


def _validate_dependencies(ops: list[OperatorSpec]) -> None:
    """Raise if any depends_on references an unknown package or creates a cycle."""
    known = {op.package for op in ops}
    for op in ops:
        for dep in op.depends_on:
            if dep not in known:
                raise ValueError(
                    f"Operator '{op.name}' depends on unknown package '{dep}'. "
                    f"Known packages: {sorted(known)}"
                )

    visited: set[str] = set()
    path: set[str] = set()
    by_pkg = {op.package: op for op in ops}

    def _walk(pkg: str) -> None:
        if pkg in path:
            raise ValueError(f"Dependency cycle detected involving '{pkg}'")
        if pkg in visited:
            return
        path.add(pkg)
        for dep in by_pkg[pkg].depends_on:
            _walk(dep)
        path.discard(pkg)
        visited.add(pkg)

    for op in ops:
        _walk(op.package)


def _install_single_operator(
    oc: OcRunner,
    op: OperatorSpec,
    operator_timeout: int,
) -> None:
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
        approve_install_plan(oc, op.namespace, op.starting_csv, timeout=operator_timeout)
        wait_for_csv_by_name(oc, op.namespace, op.starting_csv, timeout=operator_timeout)
    else:
        wait_for_csv(oc, op.namespace, timeout=operator_timeout)


def _can_parallelize(ops: list[OperatorSpec]) -> bool:
    return len(ops) > 1


def _install_operators_parallel(
    oc: OcRunner,
    ops: list[OperatorSpec],
    operator_timeout: int,
    progress: ProgressTracker,
    step_offset: int,
) -> None:
    """Install operators respecting depends_on, running independent ones concurrently."""
    done_events: dict[str, threading.Event] = {op.package: threading.Event() for op in ops}
    errors: dict[str, Exception] = {}
    lock = threading.Lock()

    op_step_index = {op.package: step_offset + i for i, op in enumerate(ops)}

    def _worker(op: OperatorSpec) -> None:
        for dep in op.depends_on:
            done_events[dep].wait()
            with lock:
                if dep in errors:
                    raise RuntimeError(
                        f"Skipping '{op.name}': dependency '{dep}' failed"
                    )

        step_idx = op_step_index[op.package]
        with lock:
            progress.step(step_idx)

        if is_operator_installed(oc, op.namespace, op.package):
            logger.info("%s already installed and healthy — skipping", op.name)
            done_events[op.package].set()
            return

        _install_single_operator(oc, op, operator_timeout)
        done_events[op.package].set()

    with ThreadPoolExecutor(max_workers=len(ops)) as pool:
        futures = {pool.submit(_worker, op): op for op in ops}
        for future in as_completed(futures):
            op = futures[future]
            try:
                future.result()
            except Exception as exc:
                with lock:
                    errors[op.package] = exc
                done_events[op.package].set()

    if errors:
        names = [f"{pkg}: {exc}" for pkg, exc in errors.items()]
        raise RuntimeError(
            "Operator installation failed:\n  " + "\n  ".join(names)
        )


def _install_operators_sequential(
    oc: OcRunner,
    ops: list[OperatorSpec],
    operator_timeout: int,
    progress: ProgressTracker,
    step_offset: int,
) -> None:
    for i, op in enumerate(ops):
        progress.step(step_offset + i)

        if is_operator_installed(oc, op.namespace, op.package):
            logger.info("%s already installed and healthy — skipping", op.name)
            continue

        _install_single_operator(oc, op, operator_timeout)


def install_operators(
    oc: OcRunner,
    vendor: VendorProfile,
    vendor_config: dict[str, Any],
    machine_config_role: str = "worker",
    ocp_version: str | None = None,
    timeouts: dict[str, int] | None = None,
    json_progress: bool = False,
    operators_override: list | None = None,
    skip_post_install: bool = False,
) -> None:
    """Use machine_config_role="master" for SNO.

    If operators_override is provided, installs those instead of vendor.get_operators().
    If skip_post_install is True, skips post_operator_setup and wait_for_gpu_ready
    (used for base operator installs before snapshotting).
    """
    t = {**DEFAULT_TIMEOUTS, **(timeouts or {})}

    ops = operators_override if operators_override is not None else vendor.get_operators(vendor_config)

    if any(op.depends_on for op in ops):
        _validate_dependencies(ops)

    parallel = _can_parallelize(ops)

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
    if not skip_post_install:
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

        operator_step_offset = step + 1
        if parallel:
            logger.info("Installing %d operators in parallel", len(ops))
            _install_operators_parallel(
                oc, ops, t["operator"], progress, operator_step_offset,
            )
        else:
            _install_operators_sequential(
                oc, ops, t["operator"], progress, operator_step_offset,
            )

        step = operator_step_offset + len(ops)

        if not skip_post_install:
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
