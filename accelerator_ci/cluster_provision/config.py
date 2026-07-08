"""OpenShift cluster configuration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

VERSION_CHANNEL = "stable"


@dataclass
class RemoteConfig:
    host: str | None
    user: str
    ssh_key_path: str | None


@dataclass
class NodeConfig:
    numcpus: int
    memory: int


@dataclass
class OperatorsConfig:
    machine_config_role: str
    vendor_config: dict[str, Any]


@dataclass
class MustGatherConfig:
    artifact_dir: str


@dataclass
class ClusterConfig:
    ocp_version: str
    pull_secret_path: str
    cluster_name: str
    domain: str
    ctlplanes: int
    workers: int
    ctlplane: NodeConfig
    worker: NodeConfig
    disk_size: int
    network: str
    api_ip: str
    remote: RemoteConfig
    pci_devices: list[str]
    wait_timeout: int
    version_channel: str
    vendor: str
    operators: OperatorsConfig
    must_gather: MustGatherConfig


def _expand_path(path: str | None) -> str | None:
    if path is None:
        return None
    return os.path.expanduser(os.path.expandvars(path))


def get_kcli_params(config: ClusterConfig, tag: str) -> dict:
    """Build kcli parameters dict. tag may differ from config.ocp_version if auto-resolved."""
    return {
        "cluster": config.cluster_name,
        "domain": config.domain,
        "network": config.network,
        "ctlplanes": config.ctlplanes,
        "workers": config.workers,
        "ctlplane_memory": config.ctlplane.memory,
        "ctlplane_numcpus": config.ctlplane.numcpus,
        "worker_memory": config.worker.memory,
        "worker_numcpus": config.worker.numcpus,
        "disk_size": config.disk_size,
        "tag": tag,
        "pull_secret": config.pull_secret_path,
        "api_ip": config.api_ip,
        "version": config.version_channel,
    }


def get_cluster_topology_description(ctlplanes: int, workers: int) -> str:
    if ctlplanes == 1 and workers == 0:
        return "SNO (Single Node OpenShift)"
    return f"{ctlplanes} control plane(s) + {workers} worker(s)"


def print_config(params: dict) -> None:
    ctlplanes = params["ctlplanes"]
    workers = params["workers"]
    topology = get_cluster_topology_description(ctlplanes, workers)

    lines = ["=" * 60, f"OpenShift Cluster Configuration [{topology}]", "=" * 60]
    for key, value in params.items():
        lines.append(f"  {key}: {value}")
    lines.append("=" * 60)
    logger.info("%s", "\n".join(lines))


def load_config_file(config_path: str | Path) -> dict[str, Any]:
    config_path = Path(config_path).expanduser()
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    return config or {}


def parse_config(raw_config: dict[str, Any]) -> ClusterConfig:
    """Parse raw YAML dictionary into ClusterConfig.

    Only cluster_name and ocp_version are required.  Everything else
    falls back to sensible defaults so that bring-your-own-cluster
    users can get away with a two-key config file.
    """
    try:
        remote_data = raw_config.get("remote", {})
        remote = RemoteConfig(
            host=remote_data.get("host"),
            user=remote_data.get("user", "root"),
            ssh_key_path=_expand_path(remote_data.get("ssh_key_path")),
        )

        ctlplane_data = raw_config.get("ctlplane", {})
        ctlplane = NodeConfig(
            numcpus=ctlplane_data.get("numcpus", 4),
            memory=ctlplane_data.get("memory", 8192),
        )

        worker_data = raw_config.get("worker", {})
        worker = NodeConfig(
            numcpus=worker_data.get("numcpus", 4),
            memory=worker_data.get("memory", 8192),
        )

        pci_devices = raw_config.get("pci_devices") or []
        if isinstance(pci_devices, str):
            pci_devices = [d.strip() for d in pci_devices.replace(",", " ").split() if d.strip()]

        operators_data = raw_config.get("operators", {})
        vendor_config = {
            k: v for k, v in operators_data.items()
            if k not in ("install", "machine_config_role")
        }
        operators = OperatorsConfig(
            machine_config_role=operators_data.get("machine_config_role", "worker"),
            vendor_config=vendor_config,
        )

        must_gather_data = raw_config.get("must_gather", {})
        must_gather = MustGatherConfig(
            artifact_dir=_expand_path(must_gather_data.get("artifact_dir", "./must-gather-output")),
        )

        return ClusterConfig(
            ocp_version=raw_config["ocp_version"],
            pull_secret_path=_expand_path(raw_config.get("pull_secret_path", "")),
            cluster_name=raw_config["cluster_name"],
            domain=raw_config.get("domain", "example.com"),
            ctlplanes=raw_config.get("ctlplanes", 1),
            workers=raw_config.get("workers", 0),
            ctlplane=ctlplane,
            worker=worker,
            disk_size=raw_config.get("disk_size", 120),
            network=raw_config.get("network", "default"),
            api_ip=raw_config.get("api_ip", ""),
            remote=remote,
            pci_devices=pci_devices,
            wait_timeout=raw_config.get("wait_timeout", 3600),
            version_channel=raw_config.get("version_channel", "stable"),
            vendor=raw_config.get("vendor", ""),
            operators=operators,
            must_gather=must_gather,
        )
    except KeyError as exc:
        raise KeyError(
            f"Missing required config key: {exc}. "
            f"Minimum required: cluster_name, ocp_version."
        ) from exc


def validate_deploy_config(config: ClusterConfig) -> None:
    """Catch missing kcli fields early so we don't waste 30 min on a doomed deploy."""
    problems: list[str] = []
    if not config.pull_secret_path:
        problems.append("pull_secret_path is required for deploy")
    if not config.api_ip:
        problems.append("api_ip is required for deploy")
    if config.domain == "example.com":
        problems.append("domain is still the placeholder 'example.com'")
    if problems:
        raise RuntimeError(
            "Deploy config validation failed:\n  - " + "\n  - ".join(problems)
        )


def load_cluster_config(config_path: str | Path) -> ClusterConfig:
    raw_config = load_config_file(config_path)
    return parse_config(raw_config)
