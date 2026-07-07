from __future__ import annotations

import logging

import pytest

from accelerator_ci.cluster_provision.main import parse_args, _configure_logging


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


class TestMainRequiresVendor:
    def test_operators_without_vendor_module(self, tmp_path):
        from accelerator_ci.cluster_provision.main import main
        import yaml

        config = tmp_path / "config.yaml"
        config.write_text(yaml.dump({
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
        }))

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
