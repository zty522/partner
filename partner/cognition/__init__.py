"""Partner-owned cognitive modelling capabilities.

Incubated implementations live here only after their contracts and tests are
accepted.  Nothing in this package imports the partner_test incubator.
"""

from .world_model import (
    AssociativeWorldMemory,
    LibraryHypothesisProvider,
    TransformerHypothesisProvider,
    WorldModelEngine,
    WorldModelEventLedger,
)
from .active_learning import (
    ActiveLearningMemory,
    ActiveLearningOption,
    expected_information_gain,
    gaussian_information_gain,
    rank_active_learning_options,
)

__all__ = [
    "AssociativeWorldMemory",
    "LibraryHypothesisProvider",
    "TransformerHypothesisProvider",
    "WorldModelEngine",
    "WorldModelEventLedger",
    "ActiveLearningMemory",
    "ActiveLearningOption",
    "expected_information_gain",
    "gaussian_information_gain",
    "rank_active_learning_options",
]
