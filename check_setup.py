try:
    import fastapi
    import uvicorn
    import sqlalchemy
    import multipart
    from azure.storage.blob import BlobServiceClient
    
    print("Setup Successful")
except ImportError as e:
    print(f"Setup Incomplete. Missing package: {e}")
