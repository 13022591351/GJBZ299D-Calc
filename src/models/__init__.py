"""Model families registered by the calculation service."""

from .baseline import MODELS as BASELINE
from .counting_nonoperating import MODELS as COUNTING_NONOPERATING
from .counting_working import MODELS as COUNTING_WORKING
from .domestic_active import MODELS as DOMESTIC_ACTIVE
from .domestic_other import MODELS as DOMESTIC_OTHER
from .domestic_passive import MODELS as DOMESTIC_PASSIVE
from .imported_active import MODELS as IMPORTED_ACTIVE
from .imported_other import MODELS as IMPORTED_OTHER
from .imported_passive import MODELS as IMPORTED_PASSIVE
from .nonoperating import MODELS as NONOPERATING

MODEL_GROUPS = (BASELINE, DOMESTIC_ACTIVE, DOMESTIC_PASSIVE, DOMESTIC_OTHER, IMPORTED_PASSIVE,
                IMPORTED_OTHER, IMPORTED_ACTIVE, NONOPERATING, COUNTING_WORKING, COUNTING_NONOPERATING)
