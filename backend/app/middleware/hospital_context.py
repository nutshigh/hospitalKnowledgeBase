from contextvars import ContextVar
from typing import Optional

current_hospital_id: ContextVar[Optional[str]] = ContextVar("current_hospital_id", default=None)


def set_current_hospital_id(hospital_id: str):
    current_hospital_id.set(hospital_id)


def get_current_hospital_id() -> Optional[str]:
    return current_hospital_id.get()
