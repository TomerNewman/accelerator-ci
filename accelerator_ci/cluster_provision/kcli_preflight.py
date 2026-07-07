import logging
import shutil
from pathlib import Path
from accelerator_ci.cluster_provision.common import DeployError, run

logger = logging.getLogger(__name__)

def ensure_kcli_installed() -> None:
    if shutil.which("kcli") is None:
        raise DeployError("kcli is not installed or not in PATH.")

def ensure_pull_secret_exists(pull_secret_path: Path) -> None:
    if not pull_secret_path.is_file():
        raise DeployError(f"Pull secret not found: {pull_secret_path}")

def ensure_kcli_config() -> None:
    home = Path.home()
    kcli_dir = home / ".kcli"
    kcli_dir.mkdir(parents=True, exist_ok=True)

    config_file = kcli_dir / "config.yml"
    if not config_file.is_file():
        logger.info("No ~/.kcli/config.yml found. Creating a default local kvm client...")
        run(["kcli", "create", "host", "kvm", "-H", "127.0.0.1", "local"])
