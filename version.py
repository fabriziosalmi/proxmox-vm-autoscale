"""Single source of truth for the release version.

The service had no version constant at all: it logged "Starting VM Autoscaler"
with no version, exposed a `build_info` metric with no version label, and was
installed by cloning a moving branch. Answering "what is running on that node?"
required going to the node and reading git history.

Keep this in step with the git tag and with `pyproject.toml`.
"""

__version__ = "1.6.0"
