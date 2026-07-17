from collections.abc import Iterator
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from .settings import settings


@lru_cache
def client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name=settings.s3_region,
    )


def ensure_bucket() -> None:
    try:
        client().head_bucket(Bucket=settings.s3_bucket)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        kwargs = {"Bucket": settings.s3_bucket}
        if settings.s3_region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": settings.s3_region}
        client().create_bucket(**kwargs)


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    client().put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def put_fileobj(key: str, source, content_type: str = "application/octet-stream") -> None:
    client().upload_fileobj(
        source,
        settings.s3_bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )


def iter_bytes(key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    body = client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"]
    try:
        while chunk := body.read(chunk_size):
            yield chunk
    finally:
        body.close()


def copy_to_fileobj(key: str, destination, chunk_size: int = 1024 * 1024) -> None:
    for chunk in iter_bytes(key, chunk_size):
        destination.write(chunk)


def get_bytes(key: str) -> bytes:
    return client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()


def delete_keys(keys: list[str]) -> None:
    unique = list(dict.fromkeys(key for key in keys if key))
    for offset in range(0, len(unique), 1000):
        response = client().delete_objects(
            Bucket=settings.s3_bucket,
            Delete={
                "Objects": [{"Key": key} for key in unique[offset : offset + 1000]],
                "Quiet": True,
            },
        )
        if response.get("Errors"):
            raise RuntimeError("one or more stored objects could not be deleted")


def presigned_get(key: str, expires: int = 900) -> str:
    return client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires,
    )
