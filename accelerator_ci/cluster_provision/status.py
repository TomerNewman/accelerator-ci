"""Cluster status snapshot: version, nodes, operators, GPU resources."""

from __future__ import annotations

import json
import logging

from accelerator_ci.shared.oc_runner import OcRunner

logger = logging.getLogger(__name__)

_ROLE_LABEL_PREFIX = "node-role.kubernetes.io/"


def get_cluster_version(oc: OcRunner) -> str:
    r = oc.oc(
        "get", "clusterversion", "version",
        "-o", "jsonpath={.status.desired.version}", timeout=15,
    )
    if r.returncode != 0:
        return f"(unavailable: {(r.stderr or '').strip()[:80]})"
    return (r.stdout or "").strip() or "(unknown)"


def get_node_status(oc: OcRunner) -> list[dict]:
    r = oc.oc("get", "nodes", "-o", "json", timeout=15)
    if r.returncode != 0:
        return []
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return []

    nodes = []
    for node in data.get("items", []):
        labels = node.get("metadata", {}).get("labels", {})
        roles = sorted(
            k.removeprefix(_ROLE_LABEL_PREFIX)
            for k in labels if k.startswith(_ROLE_LABEL_PREFIX)
        )
        conditions = node.get("status", {}).get("conditions", [])
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in conditions
        )
        nodes.append({
            "name": node.get("metadata", {}).get("name", "?"),
            "ready": ready,
            "roles": ",".join(roles) if roles else "<none>",
            "version": node.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", ""),
        })
    return nodes


def get_installed_operators(oc: OcRunner) -> list[dict]:
    r = oc.oc(
        "get", "csv", "-A", "--no-headers",
        "-o", "custom-columns="
        "NAMESPACE:.metadata.namespace,"
        "NAME:.metadata.name,"
        "PHASE:.status.phase",
        timeout=15,
    )
    if r.returncode != 0:
        return []
    ops = []
    for line in (r.stdout or "").strip().splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        ops.append({
            "namespace": parts[0],
            "name": parts[1],
            "phase": parts[2],
        })
    return ops


def get_gpu_resources(oc: OcRunner) -> dict[str, dict[str, int]]:
    """Scan node .status.allocatable for extended resources ending in /gpu."""
    r = oc.oc("get", "nodes", "-o", "json", timeout=15)
    if r.returncode != 0:
        return {}
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}

    result: dict[str, dict[str, int]] = {}
    for node in data.get("items", []):
        name = node.get("metadata", {}).get("name", "?")
        allocatable = node.get("status", {}).get("allocatable", {})
        gpus = {}
        for key, val in allocatable.items():
            if key.endswith("/gpu") or key.endswith("/vgpu"):
                try:
                    gpus[key] = int(val)
                except (ValueError, TypeError):
                    gpus[key] = 0
        if gpus:
            result[name] = gpus
    return result


def print_status(oc: OcRunner) -> int:
    """Print a cluster status report. Returns 0 on success, 1 if API unreachable."""
    version = get_cluster_version(oc)

    nodes = get_node_status(oc)
    if not nodes and version.startswith("(unavailable"):
        logger.error("Cannot reach cluster API")
        return 1

    lines = [
        "=" * 60,
        "Cluster Status",
        "=" * 60,
        f"  OCP version: {version}",
        "",
        "  Nodes:",
    ]

    if nodes:
        for n in nodes:
            status = "Ready" if n["ready"] else "NOT Ready"
            lines.append(f"    {n['name']:40s} {status:12s} {n['roles']:20s} {n['version']}")
    else:
        lines.append("    (none found)")

    ops = get_installed_operators(oc)
    lines.append("")
    lines.append("  Installed Operators (CSVs):")
    if ops:
        for op in ops:
            lines.append(f"    {op['name']:50s} {op['phase']:12s} ({op['namespace']})")
    else:
        lines.append("    (none found)")

    gpu_resources = get_gpu_resources(oc)
    lines.append("")
    lines.append("  GPU Resources:")
    if gpu_resources:
        for node_name, gpus in gpu_resources.items():
            for key, count in gpus.items():
                lines.append(f"    {node_name:40s} {key}: {count}")
    else:
        lines.append("    (no GPU resources detected)")

    lines.append("=" * 60)
    logger.info("%s", "\n".join(lines))
    return 0
