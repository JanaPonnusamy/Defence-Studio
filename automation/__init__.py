"""NEXORA Development Framework (NDF).

An enterprise development automation framework for the NEXORA platform. NDF is a
self-contained platform module (peer of Authentication, Sync, Procurement) that
automates versioning, changelog/release generation, documentation governance,
GitHub synchronization, architecture tracking and project governance.

The package is standard-library only and never modifies application code.
"""

__all__ = ["__version__", "FRAMEWORK_NAME"]

# NDF's own framework version (independent of the platform version it manages).
__version__ = "1.0.0"
FRAMEWORK_NAME = "NEXORA Development Framework"
