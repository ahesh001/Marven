from pathlib import Path

from .core.policy import Policy
from .core.caps import CapabilityManager, CapabilityError
from .core.selfupdate import SelfUpdater

# Repo root (parent of this package)
ROOT = Path(__file__).resolve().parents[1]
# Common paths used by server integrations
PROPOSALS_DIR = ROOT / "updates" / "proposals"
APPLIED_DIR = ROOT / "updates" / "applied"

__all__ = [
    "Policy",
    "CapabilityManager",
    "CapabilityError",
    "SelfUpdater",
    "ROOT",
    "PROPOSALS_DIR",
    "APPLIED_DIR",
]
