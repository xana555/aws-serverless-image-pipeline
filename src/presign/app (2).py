"""Pre-signed upload URL Lambda (behind an API Gateway HTTP API).

Request:  GET /upload-url?filename=cat.png&contentType=image/png
Response: {"uploadUrl": "...", "key": "uploads/ab12cd34-cat.png", "expiresIn": 300}

The client then uploads the file straight to S3 with an HTTP PUT to
uploadUrl, sending the same Content-Type header it asked for.

Environment variables:
    SOURCE_BUCKET   name of the S3 source bucket
"""
import json
import os
import re
import uuid

import boto3
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "eu-north-1")
s3 = boto3.client("s3", region_name=REGION, config=Config(signature_version="s3v4"))

BUCKET = os.environ["SOURCE_BUCKET"].strip()
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
EXPIRES_IN = 300  # seconds


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    params = event.get("queryStringParameters") or {}
    filename = params.get("filename", "image")
    content_type = params.get("contentType", "")

    if content_type not in ALLOWED_TYPES:
        return respond(400, {"error": "contentType must be image/jpeg, image/png or image/webp"})

    # Keep the name safe and unique so uploads never overwrite each other
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:100]
    key = f"uploads/{uuid.uuid4().hex[:8]}-{safe_name}"

    url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=EXPIRES_IN,
    )
    return respond(200, {"uploadUrl": url, "key": key, "expiresIn": EXPIRES_IN})
