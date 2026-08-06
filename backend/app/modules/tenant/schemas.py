import re

from pydantic import BaseModel, field_validator

HOSPITAL_ID_RE = re.compile(r"^[A-Za-z0-9]{2,16}$")


class TenantCreateRequest(BaseModel):
    hospital_id: str
    hospital_name: str

    @field_validator("hospital_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not HOSPITAL_ID_RE.match(v):
            raise ValueError(
                "hospital_id must be 2-16 alphanumeric chars, no underscores"
            )
        return v

    @field_validator("hospital_name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 100:
            raise ValueError("hospital_name required, 1..100 chars")
        return v


class TenantCreateResponse(BaseModel):
    created: bool
    hospital_id: str
    db_name: str
    hospital_name: str
    is_active: int


class TenantListItem(BaseModel):
    hospital_id: str
    hospital_name: str
    is_active: int


class TenantListResponse(BaseModel):
    items: list[TenantListItem]
    total: int