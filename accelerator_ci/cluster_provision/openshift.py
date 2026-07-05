import json
import re
import urllib.request
from accelerator_ci.cluster_provision.common import DeployError
from semver import Version

RELEASES_API_URL = "https://amd64.ocp.releases.ci.openshift.org/api/v1/releasestreams/accepted"

def get_latest_ocp_version(version_tag: str, channel_name: str = "stable") -> str:
    """Query the OCP release API to resolve X.Y -> X.Y.Z (latest stable patch)."""
    if not re.match(r'^\d+\.\d+$', version_tag.strip()):
        raise ValueError(f"Invalid version tag format: '{version_tag}'. Expected format: X.Y")

    if channel_name != "stable":
        raise ValueError(f"Channel '{channel_name}' is not supported. Only 'stable' is currently supported.")

    print(f"Checking for latest OCP version for {version_tag} in {channel_name} stream...")

    req = urllib.request.Request(RELEASES_API_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())

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
