"""Base class for GPU vendor profiles."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from accelerator_ci.shared.oc_runner import OcRunner


@dataclass
class OperatorSpec:
    """A single OLM operator to install."""

    name: str
    package: str
    namespace: str
    catalog: str
    channel: str
    starting_csv: str | None = None
    manual_approval: bool = False
    all_namespaces: bool = False

    def __post_init__(self) -> None:
        if self.manual_approval and not self.starting_csv:
            raise ValueError(
                f"OperatorSpec '{self.name}': manual_approval requires starting_csv"
            )


class VendorProfile(ABC):
    """ABC that GPU vendors implement. Loaded via ``--vendor-module``."""

    # --- Abstract (must override) ---

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for log banners (e.g. ``"AMD GPU"``)."""
        ...

    @abstractmethod
    def get_operators(self, vendor_config: dict[str, Any]) -> list[OperatorSpec]:
        """Ordered list of OLM operators to install.

        *vendor_config* is the free-form dict from the ``operators:`` config
        section (everything except ``machine_config_role``).
        """
        ...

    @abstractmethod
    def post_operator_setup(
        self, oc: OcRunner, vendor_config: dict[str, Any], ocp_version: str | None,
    ) -> None:
        """Vendor setup after all operators are installed (CRs, NFD rules, etc.).

        Called after every CSV has reached ``Succeeded``.
        """
        ...

    @abstractmethod
    def wait_for_gpu_ready(self, oc: OcRunner, timeout: int = 900) -> None:
        """Block until GPU extended resources are visible on nodes.

        Raise on failure.
        """
        ...

    # --- Optional (sensible defaults) ---

    def pre_operator_setup(
        self, oc: OcRunner, vendor_config: dict[str, Any], machine_config_role: str,
    ) -> None:
        """Vendor setup before operators are installed (MachineConfigs, labels, etc.).

        *machine_config_role* is ``"worker"`` or ``"master"`` (SNO).
        """

    def cleanup(self, oc: OcRunner) -> None:
        """Reverse the operator stack installation. Called by ``cleanup`` command."""

    def get_test_path(self) -> str:
        """Path to vendor's pytest test directory. Default ``"tests"``."""
        return "tests"

    def host_setup(
        self, host: str, user: str, ssh_key: str | None, vendor_config: dict[str, Any],
    ) -> None:
        """Host-level prep before cluster deployment (drivers, IOMMU, etc.)."""

    def get_pci_devices(
        self, host: str, user: str, ssh_key: str | None, vendor_config: dict[str, Any],
    ) -> list[str]:
        """PCI addresses to passthrough. Merged with config ``pci_devices``."""
        return []
