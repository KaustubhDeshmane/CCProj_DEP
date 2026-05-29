import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# -------------------------------------------------
# Load environment variables from .env file
# -------------------------------------------------
load_dotenv()

# -------------------------------------------------
# Connection string resolution (priority order):
#
#   1. DATABASE_URL is set  →  use it as-is
#      (covers local SQLite: sqlite:///./database.db)
#
#   2. All four AZURE_SQL_* vars are set  →  build Azure SQL URL
#
#   3. Neither  →  fall back to local SQLite with a warning
# -------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # Explicit URL provided — use it directly (SQLite, Azure SQL, Postgres, etc.)
    print(f"[DB] Using DATABASE_URL from environment.")
    SQLALCHEMY_DATABASE_URL = DATABASE_URL

else:
    AZURE_SQL_SERVER   = os.getenv("AZURE_SQL_SERVER")
    AZURE_SQL_DATABASE = os.getenv("AZURE_SQL_DATABASE")
    AZURE_SQL_USER     = os.getenv("AZURE_SQL_USER")
    AZURE_SQL_PASSWORD = os.getenv("AZURE_SQL_PASSWORD")

    _azure_vars = {
        "AZURE_SQL_SERVER":   AZURE_SQL_SERVER,
        "AZURE_SQL_DATABASE": AZURE_SQL_DATABASE,
        "AZURE_SQL_USER":     AZURE_SQL_USER,
        "AZURE_SQL_PASSWORD": AZURE_SQL_PASSWORD,
    }
    _missing = [k for k, v in _azure_vars.items() if not v]

    if not _missing:
        # All four Azure vars present — build the Azure SQL connection string
        print("[DB] All AZURE_SQL_* vars found. Connecting to Azure SQL...")
        # Clean up the server string just in case it contains tcp: or port numbers
        clean_server = AZURE_SQL_SERVER.replace("tcp:", "").split(",")[0].split(":")[0]
        
        SQLALCHEMY_DATABASE_URL = (
            f"mssql+pymssql://{AZURE_SQL_USER}:{AZURE_SQL_PASSWORD}"
            f"@{clean_server}:1433/{AZURE_SQL_DATABASE}"
        )
    else:
        # No DATABASE_URL and Azure vars are incomplete — fall back to SQLite
        print(
            f"[DB] WARNING: Azure SQL vars not set ({', '.join(_missing)}). "
            "Falling back to local SQLite (database.db). "
            "Set DATABASE_URL or all AZURE_SQL_* vars for production."
        )
        SQLALCHEMY_DATABASE_URL = "sqlite:///./database.db"

# -------------------------------------------------
# SQLAlchemy engine — SQLite needs check_same_thread=False
# -------------------------------------------------
is_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} if is_sqlite else {},
    pool_pre_ping=not is_sqlite,   # pyodbc supports this; SQLite doesn't need it
    **({"pool_size": 5, "max_overflow": 10} if not is_sqlite else {}),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# -------------------------------------------------
# Dependency for FastAPI routes
# -------------------------------------------------
def get_db():
    """Provide a transactional database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
