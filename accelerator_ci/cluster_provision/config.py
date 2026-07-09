"""OpenShift cluster configuration backed by Pydantic validation."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

VERSION_CHANNEL = "stable"


def _expand_path(path: str | None) -> str | None:
    if path is None:
        return None
    return os.path.expanduser(os.path.expandvars(path))


class RemoteConfig(BaseModel):
    host: str | None = None
    user: str = "root"
    ssh_key_path: str | None = None

    @field_validator("ssh_key_path", mode="before")
    @classmethod
    def expand_ssh_key(cls, v: str | None) -> str | None:
        return _expand_path(v)


class NodeConfig(BaseModel):
    numcpus: int = 4
    memory: int = 8192


class OperatorsConfig(BaseModel):
    machine_config_role: str = "worker"
    vendor_config: dict[str, Any] = Field(default_factory=dict)


class TimeoutsConfig(BaseModel):
    prerequisite: int = 900
    registry: int = 120
    operator: int = 600
    cluster_stability: int = 900
    gpu_ready: int = 1800
    deploy: int = 3600


class MustGatherConfig(BaseModel):
    artifact_dir: str = "./must-gather-output"

    @field_validator("artifact_dir", mode="before")
    @classmethod
    def expand_artifact_dir(cls, v: str | None) -> str:
        return _expand_path(v) or "./must-gather-output"


class ClusterConfig(BaseModel):
    """Top-level cluster configuration.

    Only cluster_name and ocp_version are required. Everything else
    has defaults so BYOC users can use a two-key config file.
    """
    cluster_name: str
    ocp_version: str
    pull_secret_path: str = ""
    domain: str = "example.com"
    ctlplanes: int = 1
    workers: int = 0
    ctlplane: NodeConfig = Field(default_factory=NodeConfig)
    worker: NodeConfig = Field(default_factory=NodeConfig)
    disk_size: int = 120
    network: str = "default"
    api_ip: str = ""
    remote: RemoteConfig = Field(default_factory=RemoteConfig)
    pci_devices: list[str] = Field(default_factory=list)
    wait_timeout: int = 3600
    version_channel: str = "stable"
    vendor: str = ""
    operators: OperatorsConfig = Field(default_factory=OperatorsConfig)
    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)
    must_gather: MustGatherConfig = Field(default_factory=MustGatherConfig)

    @field_validator("pull_secret_path", mode="before")
    @classmethod
    def expand_pull_secret(cls, v: str | None) -> str:
        return _expand_path(v) or ""

    @field_validator("pci_devices", mode="before")
    @classmethod
    def normalize_pci_devices(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [d.strip() for d in v.replace(",", " ").split() if d.strip()]
        return v

    @model_validator(mode="before")
    @classmethod
    def extract_vendor_config(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Pull vendor-specific keys out of the operators section."""
        if not isinstance(data, dict):
            return data
        operators_data = data.get("operators")
        if isinstance(operators_data, dict) and "vendor_config" not in operators_data:
            vendor_config = {
                k: v for k, v in operators_data.items()
                if k not in ("install", "machine_config_role")
            }
            data = {**data, "operators": {
                "machine_config_role": operators_data.get("machine_config_role", "worker"),
                "vendor_config": vendor_config,
            }}
        return data


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
    """Validate and parse raw YAML dict into ClusterConfig.

    Raises pydantic.ValidationError with clear per-field messages
    on bad input.
    """
    return ClusterConfig(**raw_config)


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
