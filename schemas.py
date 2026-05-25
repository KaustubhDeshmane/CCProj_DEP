from pydantic import BaseModel
from datetime import datetime
from typing import Any

class PrintJobBase(BaseModel):
    user_name: str
    roll_number: str
    file_url: str
    page_count: int
    page_settings: Any
    status: str
    total_cost: float # <--- MUST BE HERE

class PrintJobResponse(PrintJobBase):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True