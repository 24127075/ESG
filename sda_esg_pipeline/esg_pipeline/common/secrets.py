"""Secret management (SDAD §4.1).

Production must source credentials from AWS Secrets Manager or HashiCorp Vault
— never a plaintext ``.env``. The backend is selected by ``SECRET_BACKEND``:

    SECRET_BACKEND=aws    -> AWS Secrets Manager (boto3)
    SECRET_BACKEND=vault  -> HashiCorp Vault (hvac)
    SECRET_BACKEND=env    -> environment variables (LOCAL DEV ONLY, default)

All backends expose the same ``get_secret(name)`` contract and cache results
in-process for the lifetime of the worker.
"""
from __future__ import annotations

import functools
import logging
import os

logger = logging.getLogger(__name__)

SECRET_BACKEND = os.getenv("SECRET_BACKEND", "env").lower()


@functools.lru_cache(maxsize=128)
def get_secret(name: str) -> str | None:
    """Resolve a secret by logical name using the configured backend."""
    if SECRET_BACKEND == "aws":
        return _get_from_aws(name)
    if SECRET_BACKEND == "vault":
        return _get_from_vault(name)
    return os.getenv(name)


def _get_from_aws(name: str) -> str | None:  # pragma: no cover - needs AWS
    import boto3

    region = os.getenv("AWS_REGION", "ap-southeast-1")
    client = boto3.client("secretsmanager", region_name=region)
    try:
        resp = client.get_secret_value(SecretId=name)
        return resp.get("SecretString")
    except Exception as exc:  # noqa: BLE001
        logger.error("AWS Secrets Manager lookup failed for %s: %s", name, exc)
        return None


def _get_from_vault(name: str) -> str | None:  # pragma: no cover - needs Vault
    import hvac

    client = hvac.Client(url=os.getenv("VAULT_ADDR"), token=os.getenv("VAULT_TOKEN"))
    mount = os.getenv("VAULT_MOUNT", "secret")
    try:
        resp = client.secrets.kv.v2.read_secret_version(path=name, mount_point=mount)
        return resp["data"]["data"].get("value")
    except Exception as exc:  # noqa: BLE001
        logger.error("Vault lookup failed for %s: %s", name, exc)
        return None
