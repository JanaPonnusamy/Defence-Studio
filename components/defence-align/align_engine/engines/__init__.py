"""Provider-neutral alignment engines and a registry."""
from align_engine.engines.base import (
    AlignmentEngine, EngineAlignResult, EngineCapabilities, EngineTokenInput,
    EngineTokenResult,
)
from align_engine.engines.deterministic import DeterministicTestAligner
from align_engine.engines.real_adapter import RealForcedAligner
from align_engine.engines.registry import EngineRegistry, default_registry

__all__ = [
    "AlignmentEngine", "EngineAlignResult", "EngineCapabilities", "EngineTokenInput",
    "EngineTokenResult", "DeterministicTestAligner", "RealForcedAligner",
    "EngineRegistry", "default_registry",
]
