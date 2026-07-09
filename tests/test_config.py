from __future__ import annotations

import pytest
import yaml
from pathlib import Path
from pydantic import ValidationError

from accelerator_ci.cluster_provision.config import (
    parse_config,
    load_config_file,
    get_kcli_params,
    validate_deploy_config,
    get_cluster_topology_description,
    ClusterConfig,
    _expand_path,
)


MINIMAL_CONFIG = {
    "ocp_version": "4.20",
    "pull_secret_path": "/tmp/pull-secret.json",
    "cluster_name": "test-cluster",
    "domain": "example.com",
    "ctlplanes": 1,
    "workers": 0,
    "ctlplane": {"numcpus": 6, "memory": 18432},
    "worker": {"numcpus": 4, "memory": 16384},
    "disk_size": 120,
    "network": "default",
    "api_ip": "192.168.122.253",
    "remote": {"host": None, "user": "root", "ssh_key_path": None},
    "pci_devices": [],
    "wait_timeout": 3600,
    "version_channel": "stable",
    "vendor": "generic",
    "operators": {"install": False, "machine_config_role": "worker"},
    "must_gather": {"artifact_dir": "./must-gather-output"},
}


class TestParseConfig:
    def test_minimal_config(self):
        config = parse_config(MINIMAL_CONFIG)
        assert config.cluster_name == "test-cluster"
        assert config.ocp_version == "4.20"
        assert config.ctlplanes == 1
        assert config.workers == 0
        assert config.remote.host is None
        assert config.operators.machine_config_role == "worker"

    def test_sno_topology(self):
        config = parse_config(MINIMAL_CONFIG)
        assert config.ctlplanes == 1
        assert config.workers == 0

    def test_multi_node(self):
        raw = {**MINIMAL_CONFIG, "ctlplanes": 3, "workers": 2}
        config = parse_config(raw)
        assert config.ctlplanes == 3
        assert config.workers == 2

    def test_remote_config(self):
        raw = {**MINIMAL_CONFIG, "remote": {
            "host": "gpu-host.example.com",
            "user": "admin",
            "ssh_key_path": "/tmp/key",
        }}
        config = parse_config(raw)
        assert config.remote.host == "gpu-host.example.com"
        assert config.remote.user == "admin"
        assert config.remote.ssh_key_path == "/tmp/key"

    def test_pci_devices_list(self):
        raw = {**MINIMAL_CONFIG, "pci_devices": ["0000:41:00.0", "0000:42:00.0"]}
        config = parse_config(raw)
        assert config.pci_devices == ["0000:41:00.0", "0000:42:00.0"]

    def test_pci_devices_string(self):
        raw = {**MINIMAL_CONFIG, "pci_devices": "0000:41:00.0, 0000:42:00.0"}
        config = parse_config(raw)
        assert config.pci_devices == ["0000:41:00.0", "0000:42:00.0"]

    def test_pci_devices_null(self):
        raw = {**MINIMAL_CONFIG, "pci_devices": None}
        config = parse_config(raw)
        assert config.pci_devices == []

    def test_vendor_config_extracted(self):
        raw = {**MINIMAL_CONFIG, "operators": {
            "machine_config_role": "worker",
            "gpu_operator_version": "1.4",
            "driver_version": "30.20.1",
        }}
        config = parse_config(raw)
        assert config.operators.vendor_config == {
            "gpu_operator_version": "1.4",
            "driver_version": "30.20.1",
        }

    def test_vendor_config_excludes_generic_keys(self):
        raw = {**MINIMAL_CONFIG, "operators": {
            "machine_config_role": "master",
            "custom_field": "value",
        }}
        config = parse_config(raw)
        assert "machine_config_role" not in config.operators.vendor_config
        assert config.operators.vendor_config == {"custom_field": "value"}

    def test_legacy_install_flag_ignored(self):
        """Configs with the old ``install`` key should still parse without error."""
        raw = {**MINIMAL_CONFIG, "operators": {
            "install": True,
            "machine_config_role": "worker",
        }}
        config = parse_config(raw)
        assert not hasattr(config.operators, "install")
        assert "install" not in config.operators.vendor_config

    def test_missing_required_key_raises(self):
        raw = {**MINIMAL_CONFIG}
        del raw["cluster_name"]
        with pytest.raises(ValidationError, match="cluster_name"):
            parse_config(raw)

    def test_defaults_when_operators_missing(self):
        raw = {**MINIMAL_CONFIG}
        del raw["operators"]
        config = parse_config(raw)
        assert config.operators.machine_config_role == "worker"

    def test_defaults_when_must_gather_missing(self):
        raw = {**MINIMAL_CONFIG}
        del raw["must_gather"]
        config = parse_config(raw)
        assert "must-gather-output" in config.must_gather.artifact_dir

    def test_byoc_minimal_config(self):
        raw = {"cluster_name": "external", "ocp_version": "4.16"}
        config = parse_config(raw)
        assert config.cluster_name == "external"
        assert config.ocp_version == "4.16"
        assert config.domain == "example.com"
        assert config.ctlplanes == 1
        assert config.workers == 0
        assert config.network == "default"
        assert config.remote.host is None
        assert config.remote.user == "root"
        assert config.operators.machine_config_role == "worker"

    def test_byoc_missing_cluster_name_raises(self):
        raw = {"ocp_version": "4.16"}
        with pytest.raises(ValidationError, match="cluster_name"):
            parse_config(raw)

    def test_byoc_missing_ocp_version_raises(self):
        raw = {"cluster_name": "test"}
        with pytest.raises(ValidationError, match="ocp_version"):
            parse_config(raw)

    def test_multiple_errors_reported_at_once(self):
        with pytest.raises(ValidationError) as exc_info:
            parse_config({})
        errors = exc_info.value.errors()
        field_names = {e["loc"][0] for e in errors}
        assert "cluster_name" in field_names
        assert "ocp_version" in field_names

    def test_wrong_type_gives_clear_error(self):
        raw = {**MINIMAL_CONFIG, "ctlplanes": "not_a_number"}
        with pytest.raises(ValidationError, match="ctlplanes"):
            parse_config(raw)


class TestTimeoutsConfig:
    def test_defaults(self):
        config = parse_config(MINIMAL_CONFIG)
        assert config.timeouts.prerequisite == 900
        assert config.timeouts.registry == 120
        assert config.timeouts.operator == 600
        assert config.timeouts.cluster_stability == 900
        assert config.timeouts.gpu_ready == 1800
        assert config.timeouts.deploy == 3600

    def test_partial_override(self):
        raw = {**MINIMAL_CONFIG, "timeouts": {"operator": 1200, "gpu_ready": 3600}}
        config = parse_config(raw)
        assert config.timeouts.operator == 1200
        assert config.timeouts.gpu_ready == 3600
        assert config.timeouts.prerequisite == 900

    def test_full_override(self):
        raw = {**MINIMAL_CONFIG, "timeouts": {
            "prerequisite": 100,
            "registry": 60,
            "operator": 300,
            "cluster_stability": 450,
            "gpu_ready": 900,
            "deploy": 1800,
        }}
        config = parse_config(raw)
        assert config.timeouts.prerequisite == 100
        assert config.timeouts.deploy == 1800

    def test_missing_timeouts_section(self):
        raw = {**MINIMAL_CONFIG}
        raw.pop("timeouts", None)
        config = parse_config(raw)
        assert config.timeouts.operator == 600


class TestValidateDeployConfig:
    _DEPLOY_CONFIG = {**MINIMAL_CONFIG, "domain": "lab.local"}

    def test_full_config_passes(self):
        config = parse_config(self._DEPLOY_CONFIG)
        validate_deploy_config(config)

    def test_missing_pull_secret_raises(self):
        raw = {**self._DEPLOY_CONFIG, "pull_secret_path": ""}
        config = parse_config(raw)
        with pytest.raises(RuntimeError, match="pull_secret_path"):
            validate_deploy_config(config)

    def test_missing_api_ip_raises(self):
        raw = {**self._DEPLOY_CONFIG, "api_ip": ""}
        config = parse_config(raw)
        with pytest.raises(RuntimeError, match="api_ip"):
            validate_deploy_config(config)

    def test_default_domain_raises(self):
        config = parse_config(MINIMAL_CONFIG)
        with pytest.raises(RuntimeError, match="domain"):
            validate_deploy_config(config)

    def test_byoc_minimal_not_deployable(self):
        raw = {"cluster_name": "external", "ocp_version": "4.16"}
        config = parse_config(raw)
        with pytest.raises(RuntimeError):
            validate_deploy_config(config)


class TestLoadConfigFile:
    def test_load_valid_file(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(MINIMAL_CONFIG))
        raw = load_config_file(config_file)
        assert raw["cluster_name"] == "test-cluster"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_config_file("/nonexistent/path.yaml")

    def test_empty_file(self, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        raw = load_config_file(config_file)
        assert raw == {}


class TestGetKcliParams:
    def test_builds_params(self):
        config = parse_config(MINIMAL_CONFIG)
        params = get_kcli_params(config, "4.20.5")
        assert params["cluster"] == "test-cluster"
        assert params["tag"] == "4.20.5"
        assert params["ctlplanes"] == 1
        assert params["ctlplane_memory"] == 18432

    def test_tag_differs_from_version(self):
        config = parse_config(MINIMAL_CONFIG)
        params = get_kcli_params(config, "4.20.99")
        assert params["tag"] == "4.20.99"


class TestTopologyDescription:
    def test_sno(self):
        assert get_cluster_topology_description(1, 0) == "SNO (Single Node OpenShift)"

    def test_multi_node(self):
        assert get_cluster_topology_description(3, 2) == "3 control plane(s) + 2 worker(s)"

    def test_single_worker(self):
        assert get_cluster_topology_description(1, 1) == "1 control plane(s) + 1 worker(s)"


class TestExpandPath:
    def test_none_returns_none(self):
        assert _expand_path(None) is None

    def test_tilde_expansion(self):
        result = _expand_path("~/test")
        assert "~" not in result
        assert result.endswith("/test")
