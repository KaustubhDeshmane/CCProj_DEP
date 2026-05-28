from sqlalchemy import Column, Integer, String, DateTime, JSON, Float
from datetime import datetime
from database import Base


class PrintJob(Base):
    __tablename__ = "print_jobs"

    id            = Column(Integer, primary_key=True, index=True)
    # Azure SQL (T-SQL) requires an explicit length for VARCHAR/NVARCHAR.
    # Using String(255) maps to NVARCHAR(255) via pyodbc/SQLAlchemy.
    user_name     = Column(String(255), index=True)
    roll_number   = Column(String(100), index=True)
    transaction_id = Column(String(100), nullable=True)
    file_url      = Column(String(500))
    page_settings = Column(JSON)
    page_count    = Column(Integer, default=1)
    status        = Column(String(50), default="Queued", index=True)
    timestamp     = Column(DateTime, default=datetime.utcnow)
    total_cost    = Column(Float)