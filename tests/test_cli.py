from __future__ import annotations

import logging

import pytest

import yaml

from accelerator_ci.cluster_provision.main import parse_args, main, _configure_logging


class TestParseArgs:
    def test_deploy(self):
        args = parse_args(["--config", "config.yaml", "deploy"])
        assert args.command == "deploy"
        assert args.config_file == "config.yaml"
        assert args.vendor_module is None

    def test_operators_with_vendor(self):
        args = parse_args([
            "--config", "config.yaml",
            "--vendor-module", "my_vendor.profile",
            "operators",
        ])
        assert args.command == "operators"
        assert args.vendor_module == "my_vendor.profile"

    def test_short_config_flag(self):
        args = parse_args(["-c", "config.yaml", "delete"])
        assert args.config_file == "config.yaml"
        assert args.command == "delete"

    def test_all_commands(self):
        for cmd in ["deploy", "delete", "operators", "test-gpu", "cleanup", "must-gather"]:
            args = parse_args(["--config", "c.yaml", cmd])
            assert args.command == cmd

    def test_missing_config_raises(self):
        with pytest.raises(SystemExit):
            parse_args(["deploy"])

    def test_no_command(self):
        args = parse_args(["--config", "config.yaml"])
        assert args.command is None

    def test_test_gpu_junit_xml(self):
        args = parse_args([
            "--config", "config.yaml",
            "test-gpu", "--junit-xml", "results/junit.xml",
        ])
        assert args.command == "test-gpu"
        assert args.junit_xml == "results/junit.xml"

    def test_test_gpu_without_junit_xml(self):
        args = parse_args(["--config", "config.yaml", "test-gpu"])
        assert args.command == "test-gpu"
        assert args.junit_xml is None

    def test_verbose_flag(self):
        args = parse_args(["--config", "config.yaml", "-v", "deploy"])
        assert args.verbose is True
        assert args.quiet is False

    def test_quiet_flag(self):
        args = parse_args(["--config", "config.yaml", "--quiet", "deploy"])
        assert args.quiet is True
        assert args.verbose is False

    def test_default_verbosity(self):
        args = parse_args(["--config", "config.yaml", "deploy"])
        assert args.verbose is False
        assert args.quiet is False

    def test_dry_run_flag(self):
        args = parse_args(["--config", "config.yaml", "--dry-run", "deploy"])
        assert args.dry_run is True

    def test_dry_run_short_flag(self):
        args = parse_args(["--config", "config.yaml", "-n", "deploy"])
        assert args.dry_run is True

    def test_dry_run_default_false(self):
        args = parse_args(["--config", "config.yaml", "deploy"])
        assert args.dry_run is False


_MINIMAL_CONFIG = {
    "ocp_version": "4.20",
    "pull_secret_path": "/tmp/ps.json",
    "cluster_name": "test",
    "domain": "example.com",
    "ctlplanes": 1,
    "workers": 0,
    "ctlplane": {"numcpus": 4, "memory": 8192},
    "worker": {"numcpus": 4, "memory": 8192},
    "disk_size": 120,
    "network": "default",
    "api_ip": "192.168.1.1",
    "remote": {"host": None, "user": "root", "ssh_key_path": None},
    "pci_devices": [],
    "wait_timeout": 3600,
    "version_channel": "stable",
}


class TestMainRequiresVendor:
    def test_operators_without_vendor_module(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(yaml.dump(_MINIMAL_CONFIG))

        rc = main(["--config", str(config), "operators"])
        assert rc == 1


class TestConfigureLogging:
    def _reset_logging(self):
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        root.setLevel(logging.WARNING)

    def setup_method(self):
        self._reset_logging()

    def teardown_method(self):
        self._reset_logging()

    def test_default_level_is_info(self):
        _configure_logging()
        assert logging.getLogger().level == logging.INFO

    def test_verbose_sets_debug(self):
        _configure_logging(verbose=True)
        assert logging.getLogger().level == logging.DEBUG

    def test_quiet_sets_warning(self):
        _configure_logging(quiet=True)
        assert logging.getLogger().level == logging.WARNING

    def test_verbose_wins_over_quiet(self):
        _configure_logging(verbose=True, quiet=True)
        assert logging.getLogger().level == logging.DEBUG


class TestDryRun:
    @pytest.fixture
    def config_file(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(_MINIMAL_CONFIG))
        return str(path)

    @pytest.fixture
    def remote_config_file(self, tmp_path):
        cfg = {**_MINIMAL_CONFIG, "remote": {"host": "gpu-host", "user": "root", "ssh_key_path": None}}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(cfg))
        return str(path)

    @pytest.fixture
    def multi_node_config_file(self, tmp_path):
        cfg = {**_MINIMAL_CONFIG, "ctlplanes": 3, "workers": 2}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(cfg))
        return str(path)

    def test_dry_run_deploy_returns_zero(self, config_file):
        rc = main(["--config", config_file, "--dry-run", "deploy"])
        assert rc == 0

    def test_dry_run_deploy_local(self, config_file, capsys):
        main(["--config", config_file, "--dry-run", "deploy"])
        out = capsys.readouterr().out
        assert "Dry-run: deploy" in out
        assert "test" in out
        assert "local" in out

    def test_dry_run_deploy_remote(self, remote_config_file, capsys):
        main(["--config", remote_config_file, "--dry-run", "deploy"])
        out = capsys.readouterr().out
        assert "remote" in out
        assert "gpu-host" in out
        assert "Setup remote libvirt" in out

    def test_dry_run_deploy_sno_topology(self, config_file, capsys):
        main(["--config", config_file, "--dry-run", "deploy"])
        out = capsys.readouterr().out
        assert "SNO" in out

    def test_dry_run_deploy_multi_node(self, multi_node_config_file, capsys):
        main(["--config", multi_node_config_file, "--dry-run", "deploy"])
        out = capsys.readouterr().out
        assert "3 control-plane" in out
        assert "2 worker(s)" in out

    def test_dry_run_delete_returns_zero(self, config_file):
        rc = main(["--config", config_file, "--dry-run", "delete"])
        assert rc == 0

    def test_dry_run_delete(self, config_file, capsys):
        main(["--config", config_file, "--dry-run", "delete"])
        out = capsys.readouterr().out
        assert "Dry-run: delete" in out
        assert "test" in out

    def test_dry_run_must_gather_returns_zero(self, config_file):
        rc = main(["--config", config_file, "--dry-run", "must-gather"])
        assert rc == 0

    def test_dry_run_must_gather_local(self, config_file, capsys):
        main(["--config", config_file, "--dry-run", "must-gather"])
        out = capsys.readouterr().out
        assert "Dry-run: must-gather" in out
        assert "must-gather.sh locally" in out

    def test_dry_run_must_gather_remote(self, remote_config_file, capsys):
        main(["--config", remote_config_file, "--dry-run", "must-gather"])
        out = capsys.readouterr().out
        assert "SCP must-gather script" in out

    def test_dry_run_operators_requires_vendor(self, config_file):
        rc = main(["--config", config_file, "--dry-run", "operators"])
        assert rc == 1

    def test_dry_run_test_gpu_requires_vendor(self, config_file):
        rc = main(["--config", config_file, "--dry-run", "test-gpu"])
        assert rc == 1

    def test_dry_run_cleanup_requires_vendor(self, config_file):
        rc = main(["--config", config_file, "--dry-run", "cleanup"])
        assert rc == 1

    def test_dry_run_deploy_skipped_with_kubeconfig(self, config_file, tmp_path, capsys):
        kc = tmp_path / "kubeconfig"
        kc.write_text("apiVersion: v1")
        rc = main(["--config", config_file, "--dry-run", "--kubeconfig", str(kc), "deploy"])
        assert rc == 0
        assert "SKIPPED" in capsys.readouterr().out

    def test_dry_run_delete_skipped_with_kubeconfig(self, config_file, tmp_path, capsys):
        kc = tmp_path / "kubeconfig"
        kc.write_text("apiVersion: v1")
        rc = main(["--config", config_file, "--dry-run", "--kubeconfig", str(kc), "delete"])
        assert rc == 0
        assert "SKIPPED" in capsys.readouterr().out


class TestKubeconfigFlag:
    def test_kubeconfig_parsed(self):
        args = parse_args(["--config", "c.yaml", "--kubeconfig", "/tmp/kc", "operators"])
        assert args.kubeconfig == "/tmp/kc"

    def test_kubeconfig_default_none(self):
        args = parse_args(["--config", "c.yaml", "deploy"])
        assert args.kubeconfig is None


class TestBYOC:
    @pytest.fixture
    def byoc_config_file(self, tmp_path):
        cfg = {"cluster_name": "external", "ocp_version": "4.16"}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(cfg))
        return str(path)

    @pytest.fixture
    def kubeconfig(self, tmp_path):
        kc = tmp_path / "kubeconfig"
        kc.write_text("apiVersion: v1")
        return str(kc)

    def test_deploy_skipped_with_kubeconfig(self, byoc_config_file, kubeconfig, capsys):
        rc = main(["--config", byoc_config_file, "--kubeconfig", kubeconfig, "deploy"])
        assert rc == 0
        assert "Skipping deploy" in capsys.readouterr().out

    def test_delete_skipped_with_kubeconfig(self, byoc_config_file, kubeconfig, capsys):
        rc = main(["--config", byoc_config_file, "--kubeconfig", kubeconfig, "delete"])
        assert rc == 0
        assert "Skipping delete" in capsys.readouterr().out

    def test_minimal_config_parses(self, byoc_config_file):
        rc = main(["--config", byoc_config_file, "--dry-run", "deploy"])
        assert rc == 0

    def test_operators_requires_vendor_in_byoc(self, byoc_config_file, kubeconfig):
        rc = main(["--config", byoc_config_file, "--kubeconfig", kubeconfig, "operators"])
        assert rc == 1

    def test_missing_kubeconfig_file_errors(self, byoc_config_file, capsys):
        rc = main(["--config", byoc_config_file, "--kubeconfig", "/nonexistent/kc", "deploy"])
        assert rc == 1
        assert "kubeconfig not found" in capsys.readouterr().out

    def test_kubeconfig_and_remote_host_mutual_exclusion(self, tmp_path):
        cfg = {
            "cluster_name": "test",
            "ocp_version": "4.16",
            "remote": {"host": "gpu-host", "user": "root"},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(cfg))
        kc = tmp_path / "kubeconfig"
        kc.write_text("apiVersion: v1")
        rc = main(["--config", str(config_path), "--kubeconfig", str(kc), "operators"])
        assert rc == 1
