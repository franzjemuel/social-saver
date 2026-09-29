from dataclasses import dataclass
from pathlib import Path
import asyncio
import boto3

@dataclass(frozen=True)
class StoredObject:
    provider: str
    key: str
    size_bytes: int

class R2Storage:
    def __init__(self, account_id, access_key_id, secret_access_key, bucket, presign_seconds=3600):
        if not all((account_id, access_key_id, secret_access_key, bucket)):
            raise RuntimeError("R2 is not configured")
        self.bucket = bucket
        self.presign_seconds = presign_seconds
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    async def put_file(self, path: Path, key: str, content_type: str | None = None) -> StoredObject:
        extra = {"ContentType": content_type} if content_type else {}
        await asyncio.to_thread(
            self.client.upload_file, str(path), self.bucket, key, ExtraArgs=extra
        )
        return StoredObject("r2", key, path.stat().st_size)

    async def download_file(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self.client.download_file, self.bucket, key, str(path))
        return path

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def presigned_get(self, key: str, expires_in: int | None = None) -> str:
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in or self.presign_seconds,
        )
