import os
from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal
import models
from azure.storage.blob import BlobServiceClient

def clear_all_jobs():
    db = SessionLocal()
    AZURE_CONNECTION_STRING = os.getenv("AZURE_CONNECTION_STRING")
    AZURE_CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME")
    
    blob_service_client = None
    if AZURE_CONNECTION_STRING:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
        
    try:
        jobs = db.query(models.PrintJob).all()
        print(f"Found {len(jobs)} jobs to delete.")
        
        for job in jobs:
            # Delete from Azure
            if blob_service_client:
                try:
                    blob_name = job.file_url.split('/')[-1].split('?')[0]
                    blob_client = blob_service_client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=blob_name)
                    blob_client.delete_blob()
                    print(f"Deleted blob: {blob_name}")
                except Exception as e:
                    pass # Blob might already be deleted
            
            # Delete from DB
            db.delete(job)
            
        db.commit()
        print("Database wiped successfully! Fresh start ready.")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    clear_all_jobs()
