from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from accelerator_ci.testing.runner import run_tests


class TestRunTests:
    def test_missing_test_dir(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="not found"):
            run_tests("/fake/kubeconfig", test_path=tmp_path / "no-such-dir")

    @patch("accelerator_ci.testing.runner.subprocess.run")
    def test_pass(self, mock_run, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        rc = run_tests("/kc", test_path=test_dir)
        assert rc == 0

    @patch("accelerator_ci.testing.runner.subprocess.run")
    def test_fail(self, mock_run, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        mock_run.return_value = subprocess.CompletedProcess([], 1)
        rc = run_tests("/kc", test_path=test_dir)
        assert rc == 1

    @patch("accelerator_ci.testing.runner.subprocess.run")
    def test_junit_xml(self, mock_run, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        xml_path = tmp_path / "output" / "results.xml"
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_tests("/kc", test_path=test_dir, junit_xml=xml_path)
        cmd = mock_run.call_args[0][0]
        assert any("--junitxml" in arg for arg in cmd)
        assert xml_path.parent.exists()
