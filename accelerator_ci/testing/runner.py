"""pytest runner for GPU verification tests (local and SSH-tunneled)."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import yaml

from accelerator_ci.shared.ssh import SSH_BASE_OPTS_LIST

logger = logging.getLogger(__name__)


def run_tests(
    kubeconfig_path: str | Path,
    test_path: str | Path = "tests",
    junit_xml: str | Path | None = None,
) -> int:
    test_dir = Path(test_path).resolve()

    if not test_dir.is_dir():
        raise FileNotFoundError(f"Test directory not found: {test_dir}")

    logger.info("%s\nRunning GPU Verification Tests (%s)\n%s", "=" * 60, test_dir, "=" * 60)

    env = {
        **os.environ,
        "KUBECONFIG": str(Path(kubeconfig_path).resolve()),
    }

    cmd = [sys.executable, "-m", "pytest", str(test_dir), "-v"]
    if junit_xml:
        xml_path = Path(junit_xml)
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        cmd += [f"--junitxml={xml_path}"]
        logger.info("JUnit XML output: %s", xml_path)

    result = subprocess.run(cmd, env=env)

    if result.returncode == 0:
        logger.info("%s\nGPU Verification Tests: ALL PASSED\n%s", "=" * 60, "=" * 60)
    else:
        logger.error("%s\nGPU Verification Tests: FAILED (exit code %d)\n%s",
                     "=" * 60, result.returncode, "=" * 60)

    return result.returncode


def run_tests_remote(
    remote_host: str,
    remote_user: str,
    kubeconfig_path: Path,
    test_path: str = "tests",
    ssh_key_path: str | None = None,
    junit_xml: str | Path | None = None,
) -> int:
    with open(kubeconfig_path) as f:
        kc = yaml.safe_load(f)

    server_url = kc["clusters"][0]["cluster"]["server"]
    parsed = urlparse(server_url)
    api_host = parsed.hostname
    api_port = parsed.port or 6443

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        local_port = s.getsockname()[1]

    ssh_opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=30",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
    ]
    if ssh_key_path:
        ssh_opts += ["-i", ssh_key_path]

    tunnel_cmd = [
        "ssh", *ssh_opts,
        "-L", f"127.0.0.1:{local_port}:{api_host}:{api_port}",
        "-N", f"{remote_user}@{remote_host}",
    ]
    logger.info("Opening SSH tunnel (local :%d -> %s:%d via %s)...", local_port, api_host, api_port, remote_host)
    tunnel = subprocess.Popen(tunnel_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    tunnel_ready = False
    for _ in range(30):
        if tunnel.poll() is not None:
            stderr = tunnel.stderr.read().decode() if tunnel.stderr else ""
            raise RuntimeError(f"SSH tunnel failed to start: {stderr}")
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=1):
                tunnel_ready = True
                break
        except OSError:
            time.sleep(1)
    if not tunnel_ready:
        tunnel.terminate()
        raise RuntimeError("SSH tunnel started but port is not reachable after 30s")

    kc["clusters"][0]["cluster"]["server"] = f"https://127.0.0.1:{local_port}"
    kc["clusters"][0]["cluster"].pop("certificate-authority-data", None)
    kc["clusters"][0]["cluster"]["insecure-skip-tls-verify"] = True

    fd, tmp_kc_name = tempfile.mkstemp(suffix=".kubeconfig", prefix="gpu-test-")
    tmp_kc_path = Path(tmp_kc_name)
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.dump(kc, fh)
        return run_tests(tmp_kc_path, test_path=test_path, junit_xml=junit_xml)
    finally:
        tmp_kc_path.unlink(missing_ok=True)
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tunnel.kill()
        if tunnel.stderr:
            tunnel.stderr.close()
        logger.info("SSH tunnel closed.")
