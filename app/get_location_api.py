from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
import boto3
import uuid
import os
from dotenv import load_dotenv
import requests
import asyncio
from engine import SessionLocal
import itertools
from sqlalchemy.orm import Session
import botocore
from models import User
from schemas import UserResponse

load_dotenv()


# Create S3 client
app = FastAPI()


# AWS S3 config (set in your environment)
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "your-bucket-name")

s3_client = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)

# this is for 30 days lifecycle policy, you can set it in the AWS S3 console as well
# response = s3_client.put_bucket_lifecycle_configuration(
#     Bucket=BUCKET_NAME,
#     LifecycleConfiguration={
#         "Rules": [
#             {
#                 "ID": "DeleteManyaTestAfter30Days",
#                 "Status": "Enabled",
#                 "Filter": {
#                     "Prefix": "dumps/gautam_Dumps/Manya_test/"
#                 },
#                 "Expiration": {
#                     "Days": 30
#                 }
#             }
#         ]
#     }
# )

s3_key = f"dumps/gautam_Dumps/Manya_test/123/raw/tk"


@app.get("/getLocation")
def generate_upload_url(file_name: str):
    req_id = str(uuid.uuid4())  # unique session/request ID
    s3_key = f"dumps/gautam_Dumps/Manya_test/{req_id}/raw/{file_name}"
    https_path = f"https://{BUCKET_NAME}.s3.amazonaws.com/{s3_key}"
    return JSONResponse(
        {
            "req_id": req_id,
            "s3_key": s3_key,
            "location": https_path,
        }
    )


# This is a dummy method, and here i have hard codedd the s3_key for now , for testing the upload function
@app.post("/post-doc")
async def post_doc(req_id: str, presigned_url: str, file_path: UploadFile = File(...)):

    file_content = await file_path.read()
    try:

        response = s3_client.put_object(
            Bucket=BUCKET_NAME, Key=s3_key, Body=file_content
        )
        return {"status": "success", "message": "File uploaded successfully"}
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "AccessDenied":
            return {"status": "failed", "reason": "no permissions"}
        else:
            return {"status": "failed", "reason": str(e)}

    #     if response.status_code == 200:

    #         return JSONResponse({
    #             "message": "File processed successfully",
    #             "s3_key": presigned_url
    #         })
    #     elif response.status_code == 403:
    #          return JSONResponse({
    #             "message": "Time expired: Presigned URL has expired",
    #             "s3_key": presigned_url
    #         }, status_code=403)
    #     else:
    #         return JSONResponse({
    #             "message": f"Failed to upload file: {response.text}",
    #             "s3_key": presigned_url
    #         }, status_code=response.status_code)

    # except requests.exceptions.RequestException as e:
    #     return JSONResponse({
    #         "message": f"Upload failed: {str(e)}"
    #     }, status_code=500)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/getResult", response_model=UserResponse)
async def get_result(req_id: str, db: Session = Depends(get_db)):

    while True:
        db.rollback()
        user = db.query(User).filter(User.request_id == req_id).first()

        if not user:
            print("No record found, waiting...")
            await asyncio.sleep(5)
            continue
        if user.status == "File Processing":
            print("File is still processing, waiting...")
            print(user.Results)
            await asyncio.sleep(5)
            continue
        if user.status == "File Processed":
            print("FILe has been processed, fetching results...")
            k = user.Results
            db.close()
            return user


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("upload:app", port=8000, reload=True)
