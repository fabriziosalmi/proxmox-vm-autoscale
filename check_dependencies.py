#!/usr/bin/env python3
"""
Report whether the importable dependencies satisfy `requirements.txt`.

Installing on a real Proxmox VE 9 node showed the problem this exists to catch:
`apt` provides paramiko 3.5.1, PyYAML 6.0.2 and requests 2.32.3, while
`requirements.txt` asks for 5.0.0, 6.0.3 and 2.34.2. `pip3` was not installed
at all, so the pip step could never run, and because that step is deliberately
non-fatal the installation completed silently on dependencies that violate its
own declared contract.

Nothing checked. The service started, ran, and worked — which is the point:
a contract nothing enforces is decoration, and the operator had no way to know.

Exits 0 when everything is satisfied, 1 when something is not. The installer
treats a failure as advisory, because a working install on slightly older
packages is better than a refused one — but it says so, loudly, with the
versions and the two ways out.
"""

import re
import sys
from importlib import metadata

REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*>=\s*([0-9][0-9A-Za-z.\-]*)")


def parse_version(text):
    """A comparable tuple from a version string, ignoring any suffix."""
    parts = []
    for chunk in text.split("."):
        digits = ""
        for character in chunk:
            if not character.isdigit():
                break
            digits += character
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def installed_version(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def read_requirements(path):
    requirements = []
    try:
        with open(path) as handle:
            for line in handle:
                match = REQUIREMENT.match(line)
                if match:
                    requirements.append((match.group(1), match.group(2)))
    except OSError as error:
        print(f"Could not read {path}: {error}", file=sys.stderr)
    return requirements


def main(argv):
    path = argv[1] if len(argv) > 1 else "requirements.txt"
    requirements = read_requirements(path)
    if not requirements:
        print(f"No requirements found in {path}; nothing to verify.")
        return 0

    problems = []
    for name, required in requirements:
        present = installed_version(name)
        if present is None:
            problems.append(f"  {name}: not installed (requires >= {required})")
        elif parse_version(present) < parse_version(required):
            problems.append(f"  {name}: {present} installed, requires >= {required}")
        else:
            print(f"  {name}: {present} (requires >= {required}) OK")

    if not problems:
        print("All declared dependencies are satisfied.")
        return 0

    print("")
    print("WARNING: installed dependencies do not meet requirements.txt:")
    for problem in problems:
        print(problem)
    print("")
    print("The service will very likely still run — these floors track the latest")
    print("release, not the minimum the code needs — but nothing has verified that")
    print("on your combination. Two ways to close the gap:")
    print("")
    print("  1. A dedicated virtualenv, and point the systemd unit at it:")
    print("       python3 -m venv /usr/local/bin/vm_autoscale/.venv")
    print("       /usr/local/bin/vm_autoscale/.venv/bin/pip install -r requirements.txt")
    print("")
    print("  2. Accept the distribution packages and know which versions you run.")
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
