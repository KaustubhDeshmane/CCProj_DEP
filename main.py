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

from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions, ContentSettings
import razorpay
from pydantic import BaseModel

from database import engine, get_db
import models, schemas

# =========================
# DATABASE INITIALIZATION
# =========================
# Using lifespan (recommended over deprecated @app.on_event).
# Tables are created automatically on first run against Azure SQL.
# If the tables already exist, create_all() is a no-op — safe to run every time.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create all tables defined in models.py if they don't exist."""
    print("[Startup] Connecting to Azure SQL and initialising schema...")
    # models.Base.metadata.create_all(bind=engine)
    print("[Startup] Schema ready.")
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
# RAZORPAY SETUP
# =========================
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")

razorpay_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

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

class OrderRequest(BaseModel):
    amount: float

@app.post("/create-order")
def create_order(request: OrderRequest):
    if not razorpay_client:
        raise HTTPException(status_code=500, detail="Razorpay is not configured")
    
    amount_in_paise = int(request.amount * 100)
    
    try:
        data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "receipt": f"receipt_{uuid.uuid4().hex[:8]}"
        }
        order = razorpay_client.order.create(data=data)
        return {"order_id": order["id"]}
    except Exception as e:
        print(f"Razorpay Order Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create Razorpay Order")

@app.post("/upload")
async def upload_file(
    name: str = Form(...),
    roll: str = Form(...),
    options: str = Form(...),
    razorpay_payment_id: str = Form(...),
    razorpay_order_id: str = Form(...),
    razorpay_signature: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # 0. Verify Razorpay Signature mathematically
    if not razorpay_client:
        raise HTTPException(status_code=500, detail="Razorpay is not configured")
        
    try:
        razorpay_client.utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature
        })
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Payment Signature")

    # 1. Parse JSON options
    try:
        settings = json.loads(options)
        if isinstance(settings, str): 
            settings = json.loads(settings)
            
        # Inject payment receipt into settings for admin visibility
        settings["razorpay_payment_id"] = razorpay_payment_id
        settings["razorpay_order_id"] = razorpay_order_id
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

    # 4. Save to Database
    try:
        db_job = models.PrintJob(
            user_name=name,
            roll_number=roll,
            file_url=file_url,
            page_count=page_count,
            page_settings=settings,
            status="Queued",
            total_cost=total_cost,
            timestamp=datetime.now() 
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
    except Exception as e:
        print(f"DB Error: {e}")
        raise HTTPException(status_code=500, detail="Database Save Failed. Delete your .db file and restart.")

    return {
        "status": "success",
        "job_id": db_job.id,
        "total_cost": total_cost,
        "pages": page_count,
        "time": db_job.timestamp.strftime("%I:%M %p")
    }

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