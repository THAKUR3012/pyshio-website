"""The nearby health network: pharmacies, medicine catalogue & price lists, prescriptions,
pharmacy quotes, medicine orders and settlements.

Everything is matched by distance: a prescription goes to verified pharmacies within
settings.NEARBY_RADIUS_KM of the patient's home, and the patient sees the cheapest offers first.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional

from shared import validators as v


class PartnerStatus(BaseModel):
    status: Literal["approved", "rejected", "suspended"]
    reason: Optional[str] = Field(default=None, max_length=500)

    _reason = field_validator("reason")(v.optional_text)
