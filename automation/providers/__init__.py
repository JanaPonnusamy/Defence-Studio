"""NDF providers: the side-effect boundary.

All filesystem, git, time and environment access flows through these providers so
that services remain deterministic and testable.
"""
