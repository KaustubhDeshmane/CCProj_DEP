from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List
import os
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.responses import FileResponse, RedirectResponse
from fastapi import Request, Header
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions, ContentSettings
import requests
import base64
import hashlib
from pydantic import BaseModel

from database import engine, get_db
import models, schemas

# =========================
# GOOGLE AUTH SETUP
# =========================
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

# =========================
# DATABASE INITIALIZATION
# =========================
# Using lifespan (recommended over deprecated @app.on_event).
# Tables are created automatically on first run against Azure SQL.
# If the tables already exist, create_all() is a no-op — safe to run every time.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create all tables defined in models.py if they don't exist."""
    try:
        print("[Startup] Connecting to Azure SQL and initialising schema...")
        models.Base.metadata.create_all(bind=engine)
        print("[Startup] Schema ready.")
    except Exception as e:
        print(f"CRITICAL DB ERROR ON STARTUP: {e}")
    yield  # App runs here
    # (Add any shutdown/cleanup logic below the yield if needed)
    print("[Shutdown] Database connections released.")


app = FastAPI(title="Cloud Print Queue System", lifespan=lifespan)

# =========================
# CORS CONFIGURATION
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# DIRECTORY SETUP
# =========================
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/static", StaticFiles(directory="static"), name="static")


# =========================
# AZURE STORAGE SETUP
# =========================
load_dotenv()
AZURE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME", "print-jobs")

blob_service_client = None
if AZURE_CONNECTION_STRING:
    try:
        blob_service_client = BlobServiceClient.from_connection_string(
            AZURE_CONNECTION_STRING
        )
        print("[Azure] Blob service initialized.")
    except Exception as e:
        print(f"[Azure Init Error] {e}")

# =========================
# PhonePe Integration
# =========================
PHONEPE_MERCHANT_ID = os.getenv("PHONEPE_MERCHANT_ID", "PGTESTPAYUAT86")
PHONEPE_SALT_KEY = os.getenv("PHONEPE_SALT_KEY", "96434309-7796-489d-8924-ab56988a6076")
PHONEPE_SALT_INDEX = os.getenv("PHONEPE_SALT_INDEX", "1")
PHONEPE_ENV = os.getenv("PHONEPE_ENV", "UAT")

def calculate_sha256_string(payload_string):
    sha256 = hashlib.sha256(payload_string.encode('utf-8')).hexdigest()
    return sha256

# =========================
# HELPER FUNCTIONS
# =========================
def calculate_job_duration_seconds(page_count: int, settings: dict) -> int:
    """Calculates realistic printer time based on settings."""
    duration = 5  # Fixed spooling/warmup time per job
    
    color_val = settings.get("color", "B&W")
    paper_size = settings.get("size", "A4")
    sides = settings.get("sides", "Single")
    copies = int(settings.get("copies", 1))
    
    time_per_page = 2 # Base time for A4 B&W Single
    if color_val == "Color":
        time_per_page += 2
    if paper_size == "A3":
        time_per_page += 3
    if sides == "Double":
        time_per_page += 3
        
    duration += (page_count * copies * time_per_page)
    return duration

# =========================
# ROUTES
# =========================

@app.get("/")
def home():
    return FileResponse("static/index.html")

@app.get("/admin")
def admin_page():
    return FileResponse("static/admin.html")

@app.post("/upload-pending")
async def upload_pending(
    request: Request,
    authorization: str = Header(None),
    settings: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # 0. Verify Google Authentication
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized: Missing or invalid token")
        
    token = authorization.split(" ")[1]
    
    try:
        if GOOGLE_CLIENT_ID:
            idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)
            user_name = idinfo.get("name", "Unknown User")
            user_email = idinfo.get("email", "Unknown Email")
            
            # Strict Domain Verification
            if not user_email.endswith("@dbit.in"):
                raise HTTPException(status_code=403, detail="Forbidden: Only @dbit.in college emails are allowed")
        else:
            # Fallback for local testing if no client ID is set (Not secure for production)
            print("WARNING: GOOGLE_CLIENT_ID not set. Skipping verification for dev mode.")
            user_name = "Dev User"
            user_email = "dev@example.com"
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid token")

    # 1. Parse JSON settings
    try:
        settings = json.loads(settings)
        if isinstance(settings, str): 
            settings = json.loads(settings)
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON format")

    # 2. Upload to Azure Blob Storage
    file_ext = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    
    file_url = ""
    if blob_service_client:
        try:
            blob_client = blob_service_client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=unique_filename)
            # Upload the incoming file stream directly without saving locally!
            blob_client.upload_blob(
                file.file, 
                content_settings=ContentSettings(content_type=file.content_type)
            )
            
            # Generate a Secure SAS Token valid for 30 days
            sas_token = generate_blob_sas(
                account_name=blob_service_client.account_name,
                container_name=AZURE_CONTAINER_NAME,
                blob_name=unique_filename,
                account_key=blob_service_client.credential.account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.now(timezone.utc) + timedelta(days=30)
            )
            file_url = f"{blob_client.url}?{sas_token}"
        except Exception as e:
            print(f"Azure Upload Error: {e}")
            raise HTTPException(status_code=500, detail="Cloud Storage Upload Failed")
    else:
        # Fallback to local storage if Azure is not configured
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        try:
            with open(file_path, "wb") as buffer:
                buffer.write(file.file.read())
            file_url = f"/uploads/{unique_filename}"
        except Exception as e:
            raise HTTPException(status_code=500, detail="Local Storage Upload Failed")

    # 3. Process Metadata & Math (using frontend page count)
    page_count = int(settings.get("pageCount", 1))
    
    # B&W = 1, Color = 10
    color_val = settings.get("color", "B&W")
    base_rate = 10 if color_val == "Color" else 1
    
    paper_size = settings.get("size", "A4")
    size_multiplier = 2 if paper_size == "A3" else 1
    
    copies = int(settings.get("copies", 1))
    
    # ACTUAL TOTAL COST MATH
    total_cost = float(base_rate * size_multiplier * page_count * copies)

    transaction_id = f"TXN_{uuid.uuid4().hex[:16]}"
    merchant_user_id = f"USER_{user_email.replace('@', '_').replace('.', '_')}"

    # 4. Save to Database
    try:
        db_job = models.PrintJob(
            user_name=user_name,
            user_email=user_email,
            file_url=file_url,
            page_count=page_count,
            page_settings=settings,
            status="Queued",  # Kept as queued, since this is simple
            total_cost=total_cost,
            transaction_id=transaction_id,
            timestamp=datetime.now() 
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
    except Exception as e:
        print(f"DB Error: {e}")
        return {"status": "error", "message": f"Database Save Failed: {e}"}

    # PhonePe Checkout Request
    amount_in_paise = int(total_cost * 100)
    base_url = str(request.base_url).rstrip("/")
    
    payload = {
        "merchantId": PHONEPE_MERCHANT_ID,
        "merchantTransactionId": transaction_id,
        "merchantUserId": merchant_user_id,
        "amount": amount_in_paise,
        "redirectUrl": f"{base_url}/payment/callback",
        "redirectMode": "POST",
        "callbackUrl": f"{base_url}/payment/callback",
        "mobileNumber": "9999999999",
        "paymentInstrument": {
            "type": "PAY_PAGE"
        }
    }
    
    payload_json = json.dumps(payload)
    base64_payload = base64.b64encode(payload_json.encode()).decode()
    
    api_endpoint = "/pg/v1/pay"
    string_to_hash = base64_payload + api_endpoint + PHONEPE_SALT_KEY
    checksum = calculate_sha256_string(string_to_hash) + "###" + PHONEPE_SALT_INDEX
    
    headers = {
        "Content-Type": "application/json",
        "X-VERIFY": checksum
    }
    
    phonepe_url = "https://api-preprod.phonepe.com/apis/pg-sandbox/pg/v1/pay" if PHONEPE_ENV == "UAT" else "https://api.phonepe.com/apis/hermes/pg/v1/pay"
    
    try:
        response = requests.post(phonepe_url, json={"request": base64_payload}, headers=headers)
        response_data = response.json()
        if response_data.get("success"):
            payment_url = response_data["data"]["instrumentResponse"]["redirectInfo"]["url"]
            return {"status": "success", "redirectUrl": payment_url}
        else:
            print("PhonePe Error:", response_data)
            return {"status": "error", "message": f"PhonePe rejected request: {response_data.get('code')}"}
    except Exception as e:
        print("Request to PhonePe failed:", e)
        return {"status": "error", "message": f"PhonePe API request failed: {e}"}

@app.post("/payment/callback")
async def payment_callback(transactionId: str = Form(...), code: str = Form(...), db: Session = Depends(get_db)):
    """Verifies the payment via PhonePe Status API and redirects back to frontend."""
    txn_id = transactionId
    
    if not txn_id:
        return RedirectResponse(url="/?success=false&reason=Missing_Transaction_ID", status_code=303)
        
    endpoint = f"/pg/v1/status/{PHONEPE_MERCHANT_ID}/{txn_id}"
    string_to_hash = endpoint + PHONEPE_SALT_KEY
    checksum = calculate_sha256_string(string_to_hash) + "###" + PHONEPE_SALT_INDEX
    
    headers = {
        "Content-Type": "application/json",
        "X-VERIFY": checksum,
        "X-MERCHANT-ID": PHONEPE_MERCHANT_ID
    }
    
    status_url = f"https://api-preprod.phonepe.com/apis/pg-sandbox{endpoint}" if PHONEPE_ENV == "UAT" else f"https://api.phonepe.com/apis/hermes{endpoint}"
    
    try:
        response = requests.get(status_url, headers=headers)
        status_data = response.json()
        
        job = db.query(models.PrintJob).filter(models.PrintJob.transaction_id == txn_id).first()
        
        if status_data.get("success") and status_data.get("code") == "PAYMENT_SUCCESS":
            if job:
                job.payment_id = status_data["data"]["transactionId"]
                db.commit()
            return RedirectResponse(url=f"/?success=true", status_code=303)
        else:
            if job:
                job.status = "Failed"
                db.commit()
            return RedirectResponse(url="/?success=false&reason=Payment_Failed", status_code=303)
    except Exception as e:
        print("Callback verification failed:", e)
        return RedirectResponse(url="/?success=false&reason=Verification_Error", status_code=303)

@app.get("/queue", response_model=List[schemas.PrintJobResponse])
def get_queue(db: Session = Depends(get_db)):
    return db.query(models.PrintJob).filter(models.PrintJob.status == "Queued").all()

@app.get("/queue/status")
def get_queue_count(db: Session = Depends(get_db)):
    jobs = db.query(models.PrintJob).filter(models.PrintJob.status == "Queued").all()
    total_seconds = 0
    for job in jobs:
        total_seconds += calculate_job_duration_seconds(job.page_count, job.page_settings)
    return {"count": len(jobs), "estimated_wait_seconds": total_seconds}

@app.patch("/complete/{job_id}")
def complete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.PrintJob).filter(models.PrintJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job.status = "Completed"
    db.commit()
    return {"message": "done"}

@app.get("/view/{filename}")
def view_file(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404)
    return FileResponse(file_path)

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)