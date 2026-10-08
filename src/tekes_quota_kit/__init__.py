"""TekesQuotaKit: shared quota admission and metering."""

from importlib.metadata import PackageNotFoundError, version

try:
    # The only place the version is written is pyproject.toml; everything else reads it here.
    __version__ = version("tekes-quota-kit")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
