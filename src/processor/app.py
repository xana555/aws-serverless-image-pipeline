"""Image processor Lambda, one function with three actions.

Step Functions calls it once per workflow step:

  {"action": "validate", "bucket": "...", "key": "..."}
      Checks the file is a real image. Records PROCESSING in DynamoDB.

  {"action": "resize", "variant": "thumbnail" | "web", "bucket": "...", "key": "..."}
      Resizes, watermarks and saves one variant to the destination bucket.
      The two variants run in parallel.

  {"action": "finalize", "key": "...", "validation": {...}, "variants": [...]}
      Records DONE and the output file names in DynamoDB.

Environment variables:
    DEST_BUCKET     name of the destination S3 bucket
    TABLE_NAME      name of the DynamoDB table (partition key: imageId)
    WATERMARK_TEXT  optional, defaults to "SnapShelf"
"""
import io
import os
import time
from urllib.parse import unquote_plus

import boto3
from PIL import Image, ImageDraw, ImageFont, ImageOps

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"].strip())

DEST_BUCKET = os.environ["DEST_BUCKET"].strip()
WATERMARK_TEXT = os.environ.get("WATERMARK_TEXT", "SnapShelf")
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
VARIANTS = {
    "thumbnail": {"size": 256, "prefix": "processed/thumbnails"},
    "web": {"size": 1024, "prefix": "processed/web"},
}


def save_status(image_id, **attrs):
    """Create or update the metadata item for this image."""
    attrs["updatedAt"] = int(time.time())
    names = {f"#{k}": k for k in attrs}
    values = {f":{k}": v for k, v in attrs.items()}
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in attrs)
    table.update_item(
        Key={"imageId": image_id},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def load_image(bucket, key):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    img = Image.open(io.BytesIO(body))
    if img.format not in ALLOWED_FORMATS:
        raise ValueError(f"Unsupported image format: {img.format}")
    img.load()
    return img, len(body)


def add_watermark(img):
    """Draw semi-transparent text in the bottom-right corner."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default(size=max(14, img.width // 20))
    left, top, right, bottom = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
    x = img.width - (right - left) - 12
    y = img.height - (bottom - top) - 12
    draw.text((x, y), WATERMARK_TEXT, font=font, fill=(255, 255, 255, 160))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def validate(event):
    key = unquote_plus(event["key"])  # S3 events URL-encode keys
    save_status(key, status="PROCESSING", originalKey=key)
    try:
        img, size_bytes = load_image(event["bucket"], key)
        return {
            "format": img.format,
            "width": img.width,
            "height": img.height,
            "bytes": size_bytes,
        }
    except Exception as exc:
        save_status(key, status="FAILED", error=str(exc)[:500])
        raise


def resize(event):
    key = unquote_plus(event["key"])
    variant = event["variant"]
    cfg = VARIANTS[variant]
    try:
        img, _ = load_image(event["bucket"], key)
        img = ImageOps.exif_transpose(img)  # apply the phone's rotation flag
        img = img.convert("RGB")
        img.thumbnail((cfg["size"], cfg["size"]), Image.Resampling.LANCZOS)
        img = add_watermark(img)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        out.seek(0)

        base = os.path.splitext(os.path.basename(key))[0]
        processed_key = f"{cfg['prefix']}/{base}.jpg"
        s3.put_object(
            Bucket=DEST_BUCKET,
            Key=processed_key,
            Body=out,
            ContentType="image/jpeg",
        )
        return {
            "variant": variant,
            "processedKey": processed_key,
            "width": img.width,
            "height": img.height,
            "bytes": out.getbuffer().nbytes,
        }
    except Exception as exc:
        save_status(key, status="FAILED", error=str(exc)[:500])
        raise


def finalize(event):
    key = unquote_plus(event["key"])
    by_variant = {v["variant"]: v for v in event["variants"]}
    original = event["validation"]
    save_status(
        key,
        status="DONE",
        originalFormat=original["format"],
        originalWidth=original["width"],
        originalHeight=original["height"],
        originalBytes=original["bytes"],
        thumbnailKey=by_variant["thumbnail"]["processedKey"],
        webKey=by_variant["web"]["processedKey"],
        webWidth=by_variant["web"]["width"],
        webHeight=by_variant["web"]["height"],
    )
    return {"imageId": key, "status": "DONE"}


ACTIONS = {"validate": validate, "resize": resize, "finalize": finalize}


def lambda_handler(event, context):
    return ACTIONS[event["action"]](event)
