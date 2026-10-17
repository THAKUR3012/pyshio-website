"""The nearby health network: pharmacies, medicine catalogue & price lists, prescriptions,
pharmacy quotes, medicine orders and settlements.

Everything is matched by distance: a prescription goes to verified pharmacies within
settings.NEARBY_RADIUS_KM of the patient's home, and the patient sees the cheapest offers first.
"""
from pydantic import BaseModel, model_validator

from shared import geo


# ---------- Request models ----------
class LocationFields(BaseModel):
    lat: float
    lng: float

    @model_validator(mode="after")
    def _point(self):
        geo.check_point(self.lat, self.lng)
        return self
