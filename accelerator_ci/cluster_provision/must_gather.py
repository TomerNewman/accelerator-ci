"""Must-gather support for local and remote clusters."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from accelerator_ci.shared.ssh import ssh_cmd, scp_cmd, get_ssh_opts, close_ssh_multiplexing
from accelerator_ci.shared.oc_runner import REMOTE_KUBECONFIG

logger = logging.getLogger(__name__)

MUST_GATHER_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "must-gather.sh"

MUST_GATHER_TIMEOUT = 600


def run_must_gather(kubeconfig: str, artifact_dir: str) -> int:
    env = {**os.environ, "KUBECONFIG": kubeconfig, "ARTIFACT_DIR": artifact_dir}
    result = subprocess.run(
        [str(MUST_GATHER_SCRIPT)], env=env, text=True, timeout=MUST_GATHER_TIMEOUT,
    )
    return result.returncode


def run_must_gather_remote(host: str, user: str, artifact_dir: str) -> int:
    remote_workdir = None
    try:
        mktemp_result = ssh_cmd(
            host, user, "mktemp -d /tmp/must-gather-XXXXXXXX", check=False, timeout=30,
        )
        if mktemp_result.returncode != 0:
            logger.error("Failed to create remote temp dir: %s", mktemp_result.stderr)
            return 1
        remote_workdir = mktemp_result.stdout.strip()

        remote_script = f"{remote_workdir}/must-gather.sh"
        remote_artifact_dir = f"{remote_workdir}/output"

        logger.info("Copying script to %s@%s:%s", user, host, remote_script)
        scp_cmd(str(MUST_GATHER_SCRIPT), f"{user}@{host}:{remote_script}")

        remote_cmd = (
            f"chmod +x {remote_script} && "
            f"KUBECONFIG={REMOTE_KUBECONFIG} "
            f"ARTIFACT_DIR={remote_artifact_dir} "
            f"{remote_script}"
        )

        logger.info("Running must-gather on %s", host)
        result = ssh_cmd(host, user, remote_cmd, check=False, timeout=MUST_GATHER_TIMEOUT)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)

        if result.returncode != 0:
            logger.error("Remote script failed with exit code %d", result.returncode)
            return result.returncode

        local_artifact = Path(artifact_dir)
        local_artifact.mkdir(parents=True, exist_ok=True)

        logger.info("Copying results from %s to %s", host, artifact_dir)
        ssh_opts = get_ssh_opts()
        tar_pipeline = [
            "ssh", *ssh_opts.split(), f"{user}@{host}",
            f"tar -cf - -C {remote_artifact_dir} .",
        ]
        tar_extract = ["tar", "-xf", "-", "-C", str(local_artifact)]
        ssh_proc = subprocess.Popen(tar_pipeline, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        extract_proc = subprocess.Popen(tar_extract, stdin=ssh_proc.stdout, stderr=subprocess.PIPE)
        ssh_proc.stdout.close()

        try:
            _, extract_err = extract_proc.communicate(timeout=300)
            ssh_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            ssh_proc.kill()
            extract_proc.kill()
            ssh_proc.wait()
            extract_proc.wait()
            logger.warning("tar pipeline timed out")
            return 1
        finally:
            if ssh_proc.stderr:
                ssh_proc.stderr.close()

        if ssh_proc.returncode != 0 or extract_proc.returncode != 0:
            logger.warning("Failed to copy results back: %s", extract_err.decode())
            return 1

        logger.info("Results saved to %s", artifact_dir)
        return 0

    finally:
        if remote_workdir:
            ssh_cmd(host, user, f"rm -rf {remote_workdir}", check=False, timeout=30)
        close_ssh_multiplexing(host, user)
