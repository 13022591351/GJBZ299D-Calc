"""Working-state stress models for domestic active devices (5.1–5.5)."""

from .domestic_active_discrete import MODELS as DISCRETE_MODELS
from .domestic_active_ic import MODELS as IC_MODELS
from .domestic_active_other import MODELS as OTHER_MODELS

MODELS = {**IC_MODELS, **DISCRETE_MODELS, **OTHER_MODELS}
