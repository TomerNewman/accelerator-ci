import logging
import re
from accelerator_ci.cluster_provision.openshift import get_latest_ocp_version
from accelerator_ci.cluster_provision.config import VERSION_CHANNEL

logger = logging.getLogger(__name__)


def update_version_to_latest_patch(version: str, channel: str = VERSION_CHANNEL) -> str:
    if not version:
        return version

    if re.match(r'^\d+\.\d+$', version):
        logger.info("Checking for latest OCP version for %s in %s channel...", version, channel)
        latest_version = get_latest_ocp_version(version, channel)
        if latest_version:
            if latest_version != version:
                logger.info("Resolved %s -> %s", version, latest_version)
            return latest_version

    return version
