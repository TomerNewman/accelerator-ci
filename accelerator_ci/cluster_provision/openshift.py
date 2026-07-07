import json
import logging
import re
import urllib.error
import urllib.request
from accelerator_ci.cluster_provision.common import DeployError
from semver import Version

logger = logging.getLogger(__name__)

RELEASES_API_URL = "https://amd64.ocp.releases.ci.openshift.org/api/v1/releasestreams/accepted"

def get_latest_ocp_version(version_tag: str, channel_name: str = "stable") -> str:
    """Resolve X.Y -> X.Y.Z via the OCP release API."""
    if not re.match(r'^\d+\.\d+$', version_tag.strip()):
        raise ValueError(f"Invalid version tag format: '{version_tag}'. Expected format: X.Y")

    if channel_name != "stable":
        raise ValueError(f"Channel '{channel_name}' is not supported. Only 'stable' is currently supported.")

    logger.info("Checking for latest OCP version for %s in %s stream...", version_tag, channel_name)

    try:
        req = urllib.request.Request(RELEASES_API_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        raise DeployError(f"Failed to fetch OCP versions from {RELEASES_API_URL}: {e}") from e
    except json.JSONDecodeError as e:
        raise DeployError(f"Invalid JSON response from OCP release API: {e}") from e

    stream_key = "4-stable"

    if stream_key not in data:
        raise DeployError(f"Stream {stream_key} not found in response.")

    versions = data[stream_key]

    prefix = version_tag + "."
    candidates = [v for v in versions if v.startswith(prefix)]

    if not candidates:
        raise DeployError(f"No versions found for {version_tag} in {channel_name} stream")

    latest = str(max(Version.parse(v) for v in candidates))
    return latest
