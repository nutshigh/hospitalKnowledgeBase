from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class DispatchConfigUpdate(BaseModel):
    max_parsing_workers: Optional[int] = None
    max_interpretation_workers: Optional[int] = None
    queue_alert_threshold: Optional[int] = None
    task_retry_max: Optional[int] = None
    task_timeout_seconds: Optional[int] = None


class ResourceMetricResponse(BaseModel):
    cpu_percent: float
    memory_percent: float
    gpu_percent: Optional[float] = None
    gpu_memory_percent: Optional[float] = None
    queue_depth_parsing: int
    queue_depth_interpretation: int
    active_workers: int


class QueueStatus(BaseModel):
    queue_name: str
    depth: int
    consumer_count: int
