"""Base class for GPU vendor profiles."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from accelerator_ci.shared.oc_runner import OcRunner


@dataclass
class OperatorSpec:
    name: str
    package: str
    namespace: str
    catalog: str
    channel: str
    starting_csv: str | None = None
    manual_approval: bool = False
    all_namespaces: bool = False


class VendorProfile(ABC):

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    def get_operators(self, vendor_config: dict[str, Any]) -> list[OperatorSpec]: ...

    @abstractmethod
    def pre_operator_setup(
        self, oc: OcRunner, vendor_config: dict[str, Any], machine_config_role: str,
    ) -> None: ...

    @abstractmethod
    def post_operator_setup(
        self, oc: OcRunner, vendor_config: dict[str, Any], ocp_version: str | None,
    ) -> None: ...

    @abstractmethod
    def wait_for_gpu_ready(self, oc: OcRunner, timeout: int = 900) -> None: ...

    @abstractmethod
    def cleanup(self, oc: OcRunner) -> None: ...

    @abstractmethod
    def get_test_path(self) -> str: ...

    def host_setup(
        self, host: str, user: str, ssh_key: str | None, vendor_config: dict[str, Any],
    ) -> None:
        """Called before cluster deployment when --vendor-module is provided."""

    def get_pci_devices(
        self, host: str, user: str, ssh_key: str | None, vendor_config: dict[str, Any],
    ) -> list[str]:
        """Return PCI addresses to passthrough. Merged with config pci_devices."""
        return []

    def resolve_operator_version(self, version: str) -> str:
        return version
