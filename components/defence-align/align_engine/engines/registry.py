"""Engine registry: resolve a config ``engine`` string to an engine instance."""
from __future__ import annotations

from align_engine.engines.base import AlignmentEngine
from align_engine.engines.deterministic import DeterministicTestAligner
from align_engine.engines.real_adapter import RealForcedAligner
from align_engine.errors import UnsupportedCapabilityError

# Stable config aliases. "deterministic-test" is the synthetic test engine;
# "real" is the gated real forced-alignment adapter.
_DETERMINISTIC = "deterministic-test"
_REAL = "real"


class EngineRegistry:
    def __init__(self):
        self._factories = {
            _DETERMINISTIC: DeterministicTestAligner,
            _REAL: RealForcedAligner,
        }

    def names(self) -> list[str]:
        return sorted(self._factories)

    def get(self, name: str) -> AlignmentEngine:
        factory = self._factories.get(name)
        if factory is None:
            raise UnsupportedCapabilityError(
                f"Unknown alignment engine {name!r}. Available: {self.names()}"
            )
        return factory()

    def capabilities(self) -> list[dict]:
        return [self.get(name).capabilities().to_dict() for name in self.names()]


def default_registry() -> EngineRegistry:
    return EngineRegistry()
