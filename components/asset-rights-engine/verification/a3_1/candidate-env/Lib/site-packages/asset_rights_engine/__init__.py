"""Asset & Rights Engine 1.1.0; optional ARE-A2 modules are explicitly imported."""
from .contracts import (AssetDescriptor, AssignmentDescriptor, RightsRecord, RightsState, AssignmentState, UseContext, SceneBinding, ReviewRequest, RevisionView, Evaluation, ReviewEvent, DomainError, digest)
from .engine import Engine
from .evaluator import evaluate_effective_rights
__version__="1.1.0"
