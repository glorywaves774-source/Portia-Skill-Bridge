"""Object storage for uploaded files.

STORAGE_BACKEND=local  files under UPLOAD_ROOT (default: instance/uploads). Fine for
                       development, or on Render with a persistent disk.
STORAGE_BACKEND=s3     any S3-compatible service: AWS S3, Cloudflare R2, Backblaze B2,
                       DigitalOcean Spaces, Wasabi. Set STORAGE_BUCKET, STORAGE_ENDPOINT_URL
                       (not needed for AWS), STORAGE_REGION, STORAGE_ACCESS_KEY_ID,
                       STORAGE_SECRET_ACCESS_KEY.

Files are never served straight from storage: every download goes through the
app, which checks sign-in, workspace and permission first. Keys always begin
with the workspace's id, so one workspace's files can't be addressed from another.
"""
from __future__ import annotations

import os
import pathlib

from flask import Flask, current_app


class StorageError(RuntimeError):
    pass


class LocalStorage:
    def __init__(self, root: str):
        self.root = pathlib.Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> pathlib.Path:
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root) + os.sep):
            raise StorageError("Invalid storage key")
        return path

    def save(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)

    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("File not found in storage") from exc

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def list(self, prefix: str) -> list[dict]:
        """Files whose key starts with prefix (a folder such as "backups/"): key, size, modified (UTC)."""
        from datetime import datetime, timezone
        folder = self._path(prefix.rstrip("/") + "/x").parent
        if not folder.is_dir():
            return []
        out = []
        for p in folder.rglob("*"):
            if p.is_file() and not p.name.endswith(".part"):
                st = p.stat()
                out.append({"key": str(p.relative_to(self.root)).replace(os.sep, "/"), "size": st.st_size,
                            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc)})
        return sorted(out, key=lambda f: f["key"])


class S3Storage:
    def __init__(self, bucket: str, *, endpoint_url=None, region=None, access_key=None, secret_key=None):
        import boto3

        if not bucket:
            raise StorageError("STORAGE_BUCKET is required for STORAGE_BACKEND=s3")
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region or None,
                                   aws_access_key_id=access_key or None, aws_secret_access_key=secret_key or None)

    def save(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                               CacheControl="private, no-store")

    def read(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey as exc:
            raise StorageError("File not found in storage") from exc

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def list(self, prefix: str) -> list[dict]:
        out = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                out.append({"key": obj["Key"], "size": obj["Size"], "modified": obj["LastModified"]})
        return sorted(out, key=lambda f: f["key"])


def init_storage(app: Flask) -> None:
    backend = app.config.get("STORAGE_BACKEND", "local")
    if backend == "s3":
        storage = S3Storage(app.config.get("STORAGE_BUCKET"), endpoint_url=app.config.get("STORAGE_ENDPOINT_URL"),
                            region=app.config.get("STORAGE_REGION"),
                            access_key=app.config.get("STORAGE_ACCESS_KEY_ID"),
                            secret_key=app.config.get("STORAGE_SECRET_ACCESS_KEY"))
    elif backend == "local":
        storage = LocalStorage(app.config.get("UPLOAD_ROOT") or os.path.join(app.instance_path, "uploads"))
    else:
        raise StorageError(f"Unknown STORAGE_BACKEND '{backend}' (use local or s3)")
    app.extensions["portia_storage"] = storage


def storage():
    return current_app.extensions["portia_storage"]
