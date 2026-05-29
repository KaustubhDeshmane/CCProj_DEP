from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, File, Form, UploadFile, HTTPException, Body
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
from apscheduler.schedulers.background import BackgroundScheduler

def cleanup_old_jobs():
    from database import SessionLocal
    print("[Cleanup] Running 24-hour cleanup task...")
    db = SessionLocal()
    try:
        # Use timezone-naive datetime to match Azure SQL defaults (or timezone.utc if you use timezone-aware)
        cutoff_time = datetime.now() - timedelta(hours=24)
        old_jobs = db.query(models.PrintJob).filter(models.PrintJob.timestamp < cutoff_time).all()
        for job in old_jobs:
            if blob_service_client:
                try:
                    blob_name = job.file_url.split('/')[-1].split('?')[0]
                    blob_client = blob_service_client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=blob_name)
                    blob_client.delete_blob()
                except Exception as e:
                    print(f"Failed to delete blob for job {job.id}: {e}")
            db.delete(job)
        db.commit()
        if old_jobs:
            print(f"[Cleanup] Successfully deleted {len(old_jobs)} expired jobs.")
    except Exception as e:
        print(f"[Cleanup] Error: {e}")
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create all tables and start background tasks."""
    try:
        print("[Startup] Connecting to Azure SQL and initialising schema...")
        models.Base.metadata.create_all(bind=engine)
        print("[Startup] Schema ready.")
    except Exception as e:
        print(f"CRITICAL DB ERROR ON STARTUP: {e}")
        
    scheduler = BackgroundScheduler()
    scheduler.add_job(cleanup_old_jobs, 'interval', hours=1)
    scheduler.start()
    
    yield  # App runs here
    
    scheduler.shutdown()
    print("[Shutdown] Cleanly stopped background tasks and database connections.")


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

def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized: Missing or invalid token")
        
    token = authorization.split(" ")[1]
    
    try:
        if GOOGLE_CLIENT_ID:
            idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)
            user_name = idinfo.get("name", "Unknown User")
            user_email = idinfo.get("email", "Unknown Email")
            
            if not user_email.endswith("@dbit.in"):
                raise HTTPException(status_code=403, detail="Forbidden: Only @dbit.in college emails are allowed")
            return {"name": user_name, "email": user_email}
        else:
            print("WARNING: GOOGLE_CLIENT_ID not set. Skipping verification for dev mode.")
            return {"name": "Dev User", "email": "dev@example.com"}
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid token")

@app.get("/my-jobs", response_model=List[schemas.PrintJobResponse])
def get_my_jobs(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs = db.query(models.PrintJob).filter(models.PrintJob.user_email == user["email"]).order_by(models.PrintJob.timestamp.desc()).all()
    
    result = []
    for job in jobs:
        progress = 0
        if job.status == "Paid":
            jobs_ahead = db.query(models.PrintJob).filter(
                models.PrintJob.status == "Paid",
                models.PrintJob.timestamp < job.timestamp
            ).count()
            
            if jobs_ahead == 0:
                progress = 90
            elif jobs_ahead == 1:
                progress = 60
            else:
                progress = 30
        elif job.status == "Completed":
            progress = 100
            
        job_data = {
            "id": job.id,
            "user_name": job.user_name,
            "user_email": job.user_email,
            "file_url": job.file_url,
            "page_count": job.page_count,
            "page_settings": job.page_settings,
            "status": job.status,
            "total_cost": job.total_cost,
            "timestamp": job.timestamp,
            "progress_percent": progress
        }
        result.append(job_data)
        
    return result

@app.delete("/admin/wipe-database")
def wipe_database(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    # Very strict security check for demo purposes
    if user.get("email") not in ["dev0039@dbit.in", "223dev0039@dbit.in"]:
        raise HTTPException(status_code=403, detail="Unauthorized. Admin access required to wipe database.")
        
    try:
        jobs = db.query(models.PrintJob).all()
        deleted_count = 0
        for job in jobs:
            if blob_service_client:
                try:
                    blob_name = job.file_url.split('/')[-1].split('?')[0]
                    blob_client = blob_service_client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=blob_name)
                    blob_client.delete_blob()
                except Exception:
                    pass
            db.delete(job)
            deleted_count += 1
            
        db.commit()
        return {"message": f"Successfully wiped {deleted_count} jobs and files from the system for a fresh start."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload-pending")
async def upload_pending(
    request: Request,
    user: dict = Depends(get_current_user),
    settings: str = Form(...),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    user_name = user["name"]
    user_email = user["email"]

    # 1. Parse JSON settings mapping { filename: { settings } }
    try:
        settings_map = json.loads(settings)
        if isinstance(settings_map, str): 
            settings_map = json.loads(settings_map)
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON format for settings")

    transaction_id = f"TXN_{uuid.uuid4().hex[:16]}"
    merchant_user_id = f"USER_{user_email.replace('@', '_').replace('.', '_')}"
    
    batch_total_cost = 0.0
    db_jobs = []

    # 2. Process each file
    for file in files:
        safe_filename = "".join([c for c in file.filename if c.isalnum() or c in " .-_"]).strip()
        unique_filename = f"{uuid.uuid4()}_{safe_filename}"
        
        file_url = ""
        if blob_service_client:
            try:
                blob_client = blob_service_client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=unique_filename)
                blob_client.upload_blob(
                    file.file, 
                    content_settings=ContentSettings(content_type=file.content_type)
                )
                
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
                print(f"Azure Upload Error for {file.filename}: {e}")
                raise HTTPException(status_code=500, detail="Cloud Storage Upload Failed")
        else:
            file_path = os.path.join(UPLOAD_DIR, unique_filename)
            try:
                with open(file_path, "wb") as buffer:
                    buffer.write(file.file.read())
                file_url = f"/uploads/{unique_filename}"
            except Exception as e:
                raise HTTPException(status_code=500, detail="Local Storage Upload Failed")

        # 3. Calculate Math for this specific file
        file_settings = settings_map.get(file.filename, {})
        page_count = int(file_settings.get("pageCount", 1))
        
        color_val = file_settings.get("color", "B&W")
        base_rate = 10 if color_val == "Color" else 1
        
        paper_size = file_settings.get("size", "A4")
        size_multiplier = 2 if paper_size == "A3" else 1
        
        copies = int(file_settings.get("copies", 1))
        
        file_total_cost = float(base_rate * size_multiplier * page_count * copies)
        batch_total_cost += file_total_cost
        
        # 4. Save to Database
        try:
            db_job = models.PrintJob(
                user_name=user_name,
                user_email=user_email,
                file_url=file_url,
                page_count=page_count,
                page_settings=file_settings,
                status="Queued",
                total_cost=file_total_cost,
                transaction_id=transaction_id,
                timestamp=datetime.now() 
            )
            db.add(db_job)
            db_jobs.append(db_job)
        except Exception as e:
            print(f"DB Error: {e}")
            return {"status": "error", "message": f"Database Save Failed: {e}"}

    try:
        db.commit()
    except Exception as e:
        return {"status": "error", "message": f"Database Commit Failed: {e}"}

    # PhonePe Checkout Request
    amount_in_paise = int(batch_total_cost * 100)
    base_url = str(request.base_url).rstrip("/")
    if "azurewebsites.net" in base_url and base_url.startswith("http://"):
        base_url = base_url.replace("http://", "https://")
    
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
        
        jobs = db.query(models.PrintJob).filter(models.PrintJob.transaction_id == txn_id).all()
        
        if status_data.get("success") and status_data.get("code") == "PAYMENT_SUCCESS":
            for job in jobs:
                job.status = "Paid"
            if jobs:
                db.commit()
            return RedirectResponse(url=f"/?success=true", status_code=303)
        else:
            for job in jobs:
                job.status = "Failed"
            if jobs:
                db.commit()
            return RedirectResponse(url="/?success=false&reason=Payment_Failed", status_code=303)
    except Exception as e:
        print("Callback verification failed:", e)
        return RedirectResponse(url="/?success=false&reason=Verification_Error", status_code=303)

@app.get("/queue", response_model=List[schemas.PrintJobResponse])
def get_queue(db: Session = Depends(get_db)):
    return db.query(models.PrintJob).filter(models.PrintJob.status == "Paid").all()

@app.get("/queue/status")
def get_queue_count(db: Session = Depends(get_db)):
    jobs = db.query(models.PrintJob).filter(models.PrintJob.status == "Paid").all()
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

@app.get("/admin/analytics")
def get_admin_analytics(db: Session = Depends(get_db)):
    jobs = db.query(models.PrintJob).filter(models.PrintJob.status.in_(["Paid", "Completed"])).all()
    total_revenue = sum(job.total_cost for job in jobs)
    completed_jobs = [job for job in jobs if job.status == "Completed"]
    
    # Format for response
    result = []
    for job in completed_jobs:
        result.append({
            "id": job.id,
            "user_name": job.user_name,
            "user_email": job.user_email,
            "file_url": job.file_url,
            "page_count": job.page_count,
            "page_settings": job.page_settings,
            "status": job.status,
            "total_cost": job.total_cost,
            "timestamp": job.timestamp
        })
    return {"total_revenue": total_revenue, "completed_jobs": sorted(result, key=lambda x: x["timestamp"], reverse=True)}

@app.post("/reprint/{job_id}")
async def reprint_job(request: Request, job_id: int, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    old_job = db.query(models.PrintJob).filter(models.PrintJob.id == job_id, models.PrintJob.user_email == user["email"]).first()
    if not old_job:
        raise HTTPException(status_code=404, detail="Original job not found")

    transaction_id = f"TXN_{uuid.uuid4().hex[:16]}"
    merchant_user_id = f"USER_{user['email'].replace('@', '_').replace('.', '_')}"

    new_job = models.PrintJob(
        user_name=old_job.user_name,
        user_email=old_job.user_email,
        file_url=old_job.file_url,
        page_count=old_job.page_count,
        page_settings=old_job.page_settings,
        status="Queued",
        total_cost=old_job.total_cost,
        transaction_id=transaction_id,
        timestamp=datetime.now()
    )
    db.add(new_job)
    db.commit()

    amount_in_paise = int(new_job.total_cost * 100)
    base_url = str(request.base_url).rstrip("/")
    if "azurewebsites.net" in base_url and base_url.startswith("http://"):
        base_url = base_url.replace("http://", "https://")
    
    payload = {
        "merchantId": PHONEPE_MERCHANT_ID,
        "merchantTransactionId": transaction_id,
        "merchantUserId": merchant_user_id,
        "amount": amount_in_paise,
        "redirectUrl": f"{base_url}/payment/callback",
        "redirectMode": "POST",
        "callbackUrl": f"{base_url}/payment/callback",
        "mobileNumber": "9999999999",
        "paymentInstrument": {"type": "PAY_PAGE"}
    }
    
    payload_json = json.dumps(payload)
    base64_payload = base64.b64encode(payload_json.encode()).decode()
    api_endpoint = "/pg/v1/pay"
    string_to_hash = base64_payload + api_endpoint + PHONEPE_SALT_KEY
    checksum = calculate_sha256_string(string_to_hash) + "###" + PHONEPE_SALT_INDEX
    
    headers = {"Content-Type": "application/json", "X-VERIFY": checksum}
    phonepe_url = "https://api-preprod.phonepe.com/apis/pg-sandbox/pg/v1/pay" if PHONEPE_ENV == "UAT" else "https://api.phonepe.com/apis/hermes/pg/v1/pay"
    
    response = requests.post(phonepe_url, json={"request": base64_payload}, headers=headers)
    response_data = response.json()
    if response_data.get("success"):
        return {"status": "success", "redirectUrl": response_data["data"]["instrumentResponse"]["redirectInfo"]["url"]}
    return {"status": "error", "message": f"PhonePe rejected request: {response_data.get('code')}"}

@app.post("/reprint-with-settings/{job_id}")
async def reprint_with_settings(request: Request, job_id: int, settings: dict = Body(...), user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    old_job = db.query(models.PrintJob).filter(models.PrintJob.id == job_id, models.PrintJob.user_email == user["email"]).first()
    if not old_job:
        raise HTTPException(status_code=404, detail="Original job not found")

    # Math
    page_count = old_job.page_count
    color_val = settings.get("color", "B&W")
    base_rate = 10 if color_val == "Color" else 1
    paper_size = settings.get("size", "A4")
    size_multiplier = 2 if paper_size == "A3" else 1
    copies = int(settings.get("copies", 1))
    total_cost = float(base_rate * size_multiplier * page_count * copies)

    transaction_id = f"TXN_{uuid.uuid4().hex[:16]}"
    merchant_user_id = f"USER_{user['email'].replace('@', '_').replace('.', '_')}"

    new_job = models.PrintJob(
        user_name=old_job.user_name,
        user_email=old_job.user_email,
        file_url=old_job.file_url,
        page_count=page_count,
        page_settings=settings,
        status="Queued",
        total_cost=total_cost,
        transaction_id=transaction_id,
        timestamp=datetime.now()
    )
    db.add(new_job)
    db.commit()

    amount_in_paise = int(total_cost * 100)
    base_url = str(request.base_url).rstrip("/")
    if "azurewebsites.net" in base_url and base_url.startswith("http://"):
        base_url = base_url.replace("http://", "https://")
    
    payload = {
        "merchantId": PHONEPE_MERCHANT_ID,
        "merchantTransactionId": transaction_id,
        "merchantUserId": merchant_user_id,
        "amount": amount_in_paise,
        "redirectUrl": f"{base_url}/payment/callback",
        "redirectMode": "POST",
        "callbackUrl": f"{base_url}/payment/callback",
        "mobileNumber": "9999999999",
        "paymentInstrument": {"type": "PAY_PAGE"}
    }
    
    payload_json = json.dumps(payload)
    base64_payload = base64.b64encode(payload_json.encode()).decode()
    api_endpoint = "/pg/v1/pay"
    string_to_hash = base64_payload + api_endpoint + PHONEPE_SALT_KEY
    checksum = calculate_sha256_string(string_to_hash) + "###" + PHONEPE_SALT_INDEX
    
    headers = {"Content-Type": "application/json", "X-VERIFY": checksum}
    phonepe_url = "https://api-preprod.phonepe.com/apis/pg-sandbox/pg/v1/pay" if PHONEPE_ENV == "UAT" else "https://api.phonepe.com/apis/hermes/pg/v1/pay"
    
    response = requests.post(phonepe_url, json={"request": base64_payload}, headers=headers)
    response_data = response.json()
    if response_data.get("success"):
        return {"status": "success", "redirectUrl": response_data["data"]["instrumentResponse"]["redirectInfo"]["url"]}
    return {"status": "error", "message": f"PhonePe rejected request: {response_data.get('code')}"}

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