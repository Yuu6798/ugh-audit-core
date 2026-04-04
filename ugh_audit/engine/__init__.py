from .calculator import build_state, compute_c, compute_delta_e, compute_grv, compute_s
from .decision import build_budget, build_policy
from .models import Budget, EngineConfig, EngineResult, Evidence, Policy, State
from .runtime import UGHAuditEngine, to_legacy_payload

# MetaPatchCompiler は PyYAML に依存するため、未インストール時はスキップ
try:
    from .metapatch import MetaPatchCompiler, MetaPatchPlan

    _HAS_METAPATCH = True
except ImportError:
    _HAS_METAPATCH = False

__all__ = [
    "Evidence",
    "State",
    "Policy",
    "Budget",
    "EngineResult",
    "EngineConfig",
    "compute_s",
    "compute_c",
    "compute_delta_e",
    "compute_grv",
    "build_state",
    "build_policy",
    "build_budget",
    "UGHAuditEngine",
    "to_legacy_payload",
]

if _HAS_METAPATCH:
    __all__ += ["MetaPatchCompiler", "MetaPatchPlan"]
