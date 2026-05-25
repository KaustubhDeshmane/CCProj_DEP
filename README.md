# ☁️ Cloud Print Queue System

A FastAPI backend that stores print jobs in **Azure SQL**, secured via environment variables.

---

## 📋 For Member 2 (DevOps) — What I Need From You

Once you have provisioned the Azure SQL resource, please share **all four** of the following values with me privately (never over chat/Slack in plaintext — use a secrets manager, encrypted DM, or 1Password):

| Variable | Where to Find It | Example |
|---|---|---|
| `AZURE_SQL_SERVER` | Azure Portal → SQL Server → Overview → **Server name** | `myserver.database.windows.net` |
| `AZURE_SQL_DATABASE` | Azure Portal → SQL Database → **Database name** | `cloud_print_db` |
| `AZURE_SQL_USER` | The SQL admin login you set during provisioning | `sqladmin` |
| `AZURE_SQL_PASSWORD` | The SQL admin password you set during provisioning | *(keep secure)* |

> ⚠️ **Important for DevOps**: Make sure the Azure SQL Server firewall rules allow connections from the developer IPs (or set "Allow Azure services" for cloud deployments). The app connects over port **1433** using **ODBC Driver 18**.

---

## 🚀 Team Setup Guide

### 1. Prerequisites

- Python 3.10+
- [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) installed on your machine

  **Windows**: Download and run the MSI from the link above.  
  **macOS**: `brew install msodbcsql18`  
  **Ubuntu/Debian**:
  ```bash
  curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
  curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list
  sudo apt-get update && sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
  ```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

New dependencies added in this migration:

| Package | Purpose |
|---|---|
| `pyodbc` | Low-level ODBC bridge between Python and Azure SQL |
| `python-dotenv` | Loads variables from `.env` into `os.getenv()` at runtime |

### 3. Configure Your Local `.env` File

```bash
# Copy the template
cp .env.example .env
```

Then open `.env` and fill in the real values provided by Member 2 (DevOps):

```dotenv
AZURE_SQL_SERVER=your-server-name.database.windows.net
AZURE_SQL_DATABASE=your-database-name
AZURE_SQL_USER=your-sql-username
AZURE_SQL_PASSWORD=your-sql-password
```

> 🔒 **Never commit `.env`** — it is already listed in `.gitignore`. Only commit `.env.example`.

### 4. Run the Application

```bash
uvicorn main:app --reload
```

On the **first run**, SQLAlchemy will automatically create the `print_jobs` table in Azure SQL if it doesn't exist yet. You'll see this in the console:

```
[Startup] Connecting to Azure SQL and initialising schema...
[Startup] Schema ready.
```

---

## 🗂️ Project Structure

```
cloud-print-system/
├── main.py          # FastAPI app + routes (uses lifespan for DB init)
├── database.py      # Azure SQL connection via SQLAlchemy + pyodbc
├── models.py        # ORM table definitions
├── schemas.py       # Pydantic request/response schemas
├── requirements.txt # Python dependencies
├── .env.example     # ✅ Commit this  — credential template
├── .env             # ❌ Never commit — real credentials
└── .gitignore
```

---

## 🔗 Key Design Decisions

- **`database.py`** fails fast at startup if any env variable is missing, so misconfiguration is caught immediately rather than at the first DB call.
- **`models.py`** uses explicit `String(N)` lengths on all text columns — required by Azure SQL (T-SQL does not accept unbounded `VARCHAR`).
- **`main.py`** uses FastAPI's `lifespan` context manager (the modern replacement for the deprecated `@app.on_event("startup")`) to run `create_all()` safely after the engine is configured.
