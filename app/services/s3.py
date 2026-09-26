import os
import uuid
import boto3

ALLOWED = {"png", "jpg", "jpeg", "txt", "pdf"}

def upload_file(file, user_id):
    name = file.filename or ""
    if "." not in name or name.rsplit(".", 1)[1].lower() not in ALLOWED:
        raise ValueError("Allowed file types: png, jpg, jpeg, txt, pdf")
    bucket = os.getenv("S3_BUCKET_NAME")
    if not bucket: raise RuntimeError("S3 is not configured (S3_BUCKET_NAME is missing)")
    key = f"uploads/{user_id}/{uuid.uuid4()}-{os.path.basename(name)}"
    boto3.client("s3", region_name=os.getenv("AWS_REGION") or None).upload_fileobj(file, bucket, key)
    return key
