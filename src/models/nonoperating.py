"""Appendix B.2 domestic nonoperating detailed prediction models."""

from .nonoperating_active import MODELS as ACTIVE_MODELS
from .nonoperating_other import MODELS as OTHER_MODELS
from .nonoperating_passive import MODELS as PASSIVE_MODELS

MODELS = ACTIVE_MODELS | PASSIVE_MODELS | OTHER_MODELS
