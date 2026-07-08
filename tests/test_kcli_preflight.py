from __future__ import annotations

from unittest.mock import patch

import pytest

from accelerator_ci.cluster_provision.common import DeployError
from accelerator_ci.cluster_provision.kcli_preflight import (
    ensure_kcli_installed,
    ensure_pull_secret_exists,
    ensure_kcli_config,
)


class TestEnsureKcliInstalled:
    @patch("accelerator_ci.cluster_provision.kcli_preflight.shutil.which", return_value="/usr/bin/kcli")
    def test_found(self, _):
        ensure_kcli_installed()

    @patch("accelerator_ci.cluster_provision.kcli_preflight.shutil.which", return_value=None)
    def test_not_found(self, _):
        with pytest.raises(DeployError, match="not installed"):
            ensure_kcli_installed()


class TestEnsurePullSecretExists:
    def test_exists(self, tmp_path):
        secret = tmp_path / "pull-secret.json"
        secret.write_text("{}")
        ensure_pull_secret_exists(secret)

    def test_missing(self, tmp_path):
        with pytest.raises(DeployError, match="not found"):
            ensure_pull_secret_exists(tmp_path / "nope.json")


class TestEnsureKcliConfig:
    @patch("accelerator_ci.cluster_provision.kcli_preflight.Path.home")
    def test_config_already_exists(self, mock_home, tmp_path):
        mock_home.return_value = tmp_path
        kcli_dir = tmp_path / ".kcli"
        kcli_dir.mkdir()
        (kcli_dir / "config.yml").write_text("existing")
        ensure_kcli_config()

    @patch("accelerator_ci.cluster_provision.kcli_preflight.run")
    @patch("accelerator_ci.cluster_provision.kcli_preflight.Path.home")
    def test_creates_default_config(self, mock_home, mock_run, tmp_path):
        mock_home.return_value = tmp_path
        ensure_kcli_config()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "kcli" in args
        assert "create" in args
