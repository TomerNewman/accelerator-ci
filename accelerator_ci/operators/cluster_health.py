"""Cluster health polling (nodes, ClusterOperators, MachineConfigPools)."""

from __future__ import annotations

import time

from accelerator_ci.operators.errors import OperatorError
from accelerator_ci.shared.oc_runner import OcRunner


def wait_for_cluster_stability(
    oc: OcRunner,
    timeout: int = 900,
    poll_interval: int = 20,
) -> None:
    """Tolerates temporary API unavailability during SNO reboots."""
    print("Waiting for cluster stability...")
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        elapsed = int(time.monotonic() - start)
        issues: list[str] = []

        r = oc.oc(
            "get", "nodes", "--no-headers",
            "-o", "custom-columns="
            "NAME:.metadata.name,"
            "READY:.status.conditions[?(@.type==\"Ready\")].status",
            timeout=15,
        )
        if r.returncode != 0:
            print(f"  API not reachable ({elapsed}s)...")
            time.sleep(poll_interval)
            continue
        for line in (r.stdout or "").strip().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] != "True":
                issues.append(f"node '{parts[0]}' not Ready")

        r = oc.oc(
            "get", "clusteroperators", "--no-headers",
            "-o", "custom-columns="
            "NAME:.metadata.name,"
            "AVAILABLE:.status.conditions[?(@.type==\"Available\")].status,"
            "PROGRESSING:.status.conditions[?(@.type==\"Progressing\")].status,"
            "DEGRADED:.status.conditions[?(@.type==\"Degraded\")].status",
            timeout=15,
        )
        if r.returncode != 0:
            print(f"  Cannot check ClusterOperators ({elapsed}s)...")
            time.sleep(poll_interval)
            continue
        for line in (r.stdout or "").strip().splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            name, available, progressing, degraded = parts[:4]
            if available != "True":
                issues.append(f"CO '{name}' not Available")
            if progressing == "True":
                issues.append(f"CO '{name}' still Progressing")
            if degraded == "True":
                issues.append(f"CO '{name}' is Degraded")

        if not issues:
            print("  Cluster is stable (all nodes Ready, all ClusterOperators healthy).")
            return

        summary = "; ".join(issues[:3])
        if len(issues) > 3:
            summary += f" (+{len(issues) - 3} more)"
        print(f"  {summary} ({elapsed}s)...")
        time.sleep(poll_interval)

    raise OperatorError(
        f"Cluster did not stabilize within {timeout}s. "
        "Check node status and ClusterOperator conditions."
    )


def wait_for_mcp_updated(
    oc: OcRunner,
    timeout: int = 900,
    poll_interval: int = 20,
) -> None:
    """Tolerates API downtime during SNO reboots from MachineConfig changes."""
    print("Waiting for MachineConfigPool to finish updating...")
    start = time.monotonic()
    saw_updating = False
    while time.monotonic() - start < timeout:
        elapsed = int(time.monotonic() - start)
        r = oc.oc(
            "get", "mcp", "--no-headers",
            "-o", "custom-columns="
            "NAME:.metadata.name,"
            "UPDATED:.status.conditions[?(@.type==\"Updated\")].status,"
            "UPDATING:.status.conditions[?(@.type==\"Updating\")].status,"
            "DEGRADED:.status.conditions[?(@.type==\"Degraded\")].status",
            timeout=15,
        )
        if r.returncode != 0:
            saw_updating = True
            print(f"  API not reachable (node likely rebooting) ({elapsed}s)...")
            time.sleep(poll_interval)
            continue

        all_updated = True
        for line in (r.stdout or "").strip().splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            name, updated, updating, degraded = parts[:4]
            if updating == "True":
                saw_updating = True
                all_updated = False
                print(f"  MCP '{name}' is still updating ({elapsed}s)...")
            elif updated != "True":
                all_updated = False
                print(f"  MCP '{name}' not yet updated ({elapsed}s)...")

        if all_updated and (r.stdout or "").strip():
            if saw_updating:
                print("  All MachineConfigPools updated, reboot complete.")
            else:
                if elapsed < 60:
                    print(f"  MCP shows updated but MCO may not have started yet ({elapsed}s)...")
                    time.sleep(poll_interval)
                    continue
                print("  All MachineConfigPools updated (MCO may have been fast).")
            return

        time.sleep(poll_interval)

    raise OperatorError(
        f"MachineConfigPool did not finish updating within {timeout}s"
    )
