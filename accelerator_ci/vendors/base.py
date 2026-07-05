"""Abstract base class for GPU vendor profiles."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from accelerator_ci.shared.oc_runner import OcRunner


@dataclass
class OperatorSpec:
    """OLM operator subscription specification."""

    name: str
    package: str
    namespace: str
    catalog: str
    channel: str
    starting_csv: str | None = None
    manual_approval: bool = False
    all_namespaces: bool = False


class VendorProfile(ABC):
    """Vendor-specific behavior for GPU operator CI."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable vendor name (e.g. "AMD GPU Operator")."""

    @abstractmethod
    def get_operators(self, vendor_config: dict[str, Any]) -> list[OperatorSpec]:
        """Return the ordered list of OLM operators to install."""

    @abstractmethod
    def pre_operator_setup(
        self,
        oc: OcRunner,
        vendor_config: dict[str, Any],
        machine_config_role: str,
    ) -> None:
        """Pre-install steps (e.g. driver blacklist MachineConfig)."""

    @abstractmethod
    def post_operator_setup(
        self,
        oc: OcRunner,
        vendor_config: dict[str, Any],
        ocp_version: str | None,
    ) -> None:
        """Post-install steps (e.g. NFD rules, vendor CRs, monitoring)."""

    @abstractmethod
    def wait_for_gpu_ready(
        self,
        oc: OcRunner,
        timeout: int = 900,
    ) -> None:
        """Wait for GPU resources to be available on nodes."""

    @abstractmethod
    def cleanup(self, oc: OcRunner) -> None:
        """Remove the vendor's operator stack."""

    @abstractmethod
    def get_test_path(self) -> str:
        """Return the path (relative to repo root) to vendor's pytest tests."""

    def resolve_operator_version(self, version: str) -> str:
        """Resolve a minor version (e.g. "1.4") to the latest patch."""
        return version
