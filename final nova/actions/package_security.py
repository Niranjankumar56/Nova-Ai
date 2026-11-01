import re

PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

BLOCKED_PACKAGE_NAMES = {
    "pip",
    "setuptools",
    "wheel",
}

def check_package_name(package: str):
    if not isinstance(package, str):
        return False, "Package name must be a string."

    package = package.strip()

    if not package:
        return False, "Empty package name."

    name = re.split(r"[<>=!~]", package, maxsplit=1)[0].strip()

    if not PACKAGE_NAME_RE.fullmatch(name):
        return False, f"Invalid package name: {name}"

    if name.lower() in BLOCKED_PACKAGE_NAMES:
        return False, f"Blocked package: {name}"

    return True, "Package name passed security checks"
