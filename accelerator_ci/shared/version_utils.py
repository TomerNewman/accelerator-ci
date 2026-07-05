from semver import Version


def max_version(a: str, b: str) -> str:
    return str(max(map(Version.parse, (a, b))))
