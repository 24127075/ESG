"""Storage layer (SDAD §1 step 5).

Persists downloaded documents to **Local Storage** and **AWS S3 with Object
Versioning enabled**. All write paths go through the directory-traversal
guards in :mod:`esg_pipeline.common.security`.
"""
from __future__ import annotations

import logging
import os

from ..common.config import settings
from ..common.security import safe_filename, safe_join

logger = logging.getLogger(__name__)


class StorageManager:
    """Local + S3 persistence with versioning support."""

    def __init__(
        self,
        local_root: str | None = None,
        s3_bucket: str | None = None,
        region: str | None = None,
    ):
        self.local_root = local_root or settings.local_storage_root
        self.s3_bucket = s3_bucket or settings.s3_bucket
        self.region = region or settings.aws_region
        self._s3 = None

    # --- Local ----------------------------------------------------------------
    def save_local(self, content: bytes, relative_dir: str, filename: str) -> str:
        """Write bytes under ``local_root/relative_dir/<safe filename>``.

        Returns the absolute path. Both the directory and the filename are
        sanitised to block path traversal.
        """
        safe_name = safe_filename(filename)
        target_dir = safe_join(self.local_root, relative_dir)
        os.makedirs(target_dir, exist_ok=True)
        abs_path = os.path.join(target_dir, safe_name)
        with open(abs_path, "wb") as f:
            f.write(content)
        logger.info("Saved %d bytes → %s", len(content), abs_path)
        return abs_path

    # --- S3 -------------------------------------------------------------------
    def _client(self):
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3", region_name=self.region)
        return self._s3

    def ensure_versioning(self) -> None:
        """Enable S3 Object Versioning on the configured bucket (idempotent)."""
        if not self.s3_bucket:
            raise ValueError("No S3 bucket configured (set S3_BUCKET).")
        self._client().put_bucket_versioning(
            Bucket=self.s3_bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )

    def upload_s3(self, content: bytes, key: str, content_type: str = "application/pdf") -> str:
        """Upload bytes to ``s3://<bucket>/<key>`` and return the version id."""
        if not self.s3_bucket:
            raise ValueError("No S3 bucket configured (set S3_BUCKET).")
        resp = self._client().put_object(
            Bucket=self.s3_bucket, Key=key, Body=content, ContentType=content_type
        )
        version_id = resp.get("VersionId", "null")
        logger.info("Uploaded s3://%s/%s (version=%s)", self.s3_bucket, key, version_id)
        return version_id
