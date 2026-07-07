from __future__ import annotations

from unittest.mock import patch, MagicMock
from pathlib import Path

from accelerator_ci.testing.runner import run_tests


class TestRunTests:
    def test_junit_xml_adds_flag(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1")
        xml_path = tmp_path / "results" / "junit.xml"

        with patch("accelerator_ci.testing.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            run_tests(kubeconfig, test_path=str(test_dir), junit_xml=str(xml_path))

        cmd = mock_run.call_args[0][0]
        assert any(arg.startswith("--junitxml=") for arg in cmd)
        assert (tmp_path / "results").is_dir()

    def test_no_junit_xml_omits_flag(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1")

        with patch("accelerator_ci.testing.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            run_tests(kubeconfig, test_path=str(test_dir))

        cmd = mock_run.call_args[0][0]
        assert not any("junitxml" in arg for arg in cmd)

    def test_junit_xml_creates_parent_dir(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1")
        xml_path = tmp_path / "deep" / "nested" / "junit.xml"

        with patch("accelerator_ci.testing.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            run_tests(kubeconfig, test_path=str(test_dir), junit_xml=str(xml_path))

        assert (tmp_path / "deep" / "nested").is_dir()

    def test_missing_test_dir_raises(self, tmp_path):
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1")

        import pytest
        with pytest.raises(FileNotFoundError, match="Test directory not found"):
            run_tests(kubeconfig, test_path=str(tmp_path / "nonexistent"))

    def test_returns_pytest_exit_code(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1")

        with patch("accelerator_ci.testing.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            rc = run_tests(kubeconfig, test_path=str(test_dir))

        assert rc == 1
