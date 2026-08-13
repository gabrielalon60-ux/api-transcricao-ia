from __future__ import annotations

import logging
from typing import Any, Dict, Optional
import httpx

from orchestrator.config import get_settings

logger = logging.getLogger(__name__)


class DBWriterClientError(Exception):
    pass


class DBWriterClient:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        settings = get_settings()
        self.base_url = str(
            base_url or getattr(settings, "db_writer_url", "http://localhost:8004")
        ).rstrip("/")
        self.token = token or getattr(
            settings, "orchestrator_to_writer_token", "dev-db-writer-token"
        )
        self.timeout = 10.0

    def _headers(self, correlation_id: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Correlation-ID": correlation_id,
            "Content-Type": "application/json",
        }

    def map_response_status(self, response_data: Dict[str, Any]) -> str:
        """Safely maps response dict to a validated status string."""
        raw_status = response_data.get("status")
        if raw_status in ("COMMITTED", "REJECTED", "RETRYABLE_FAILURE", "NOT_FOUND"):
            return raw_status
        return "OUTCOME_UNKNOWN"

    def write(
        self,
        idempotency_key: str,
        processing_item_id: str,
        organization_id: str,
        instance_id: str,
        user_id: str,
        correlation_id: str,
        document_type: str,
        payload: Dict[str, Any],
        schema_version: str = "1.0",
    ) -> Dict[str, Any]:
        """Calls POST /internal/write on Database Writer service."""
        url = f"{self.base_url}/internal/write"
        body = {
            "idempotency_key": idempotency_key,
            "processing_item_id": processing_item_id,
            "organization_id": organization_id,
            "instance_id": instance_id,
            "user_id": user_id,
            "correlation_id": correlation_id,
            "document_type": document_type,
            "payload": payload,
            "schema_version": schema_version,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    url, json=body, headers=self._headers(correlation_id)
                )
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        st = self.map_response_status(data)
                        if st == "OUTCOME_UNKNOWN":
                            return {
                                "status": "OUTCOME_UNKNOWN",
                                "error_code": "INVALID_RESPONSE_SCHEMA",
                            }
                        return data
                    except Exception:
                        return {
                            "status": "OUTCOME_UNKNOWN",
                            "error_code": "MALFORMED_JSON_RESPONSE",
                        }
                elif resp.status_code in (400, 422):
                    try:
                        data = resp.json()
                        return {
                            "status": "REJECTED",
                            "error_code": data.get(
                                "error_code", "INVALID_BUSINESS_PAYLOAD"
                            ),
                        }
                    except Exception:
                        return {
                            "status": "REJECTED",
                            "error_code": "INVALID_BUSINESS_PAYLOAD",
                        }
                elif resp.status_code == 409:
                    return {
                        "status": "REJECTED",
                        "error_code": "IDEMPOTENCY_PAYLOAD_MISMATCH",
                    }
                else:
                    return {
                        "status": "OUTCOME_UNKNOWN",
                        "error_code": f"HTTP_{resp.status_code}",
                    }
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            logger.warning(
                f"Connection failure before request transmission for item {processing_item_id}: {exc}"
            )
            return {
                "status": "RETRYABLE_FAILURE",
                "error_code": "CONNECT_FAILURE_PRE_TRANSMISSION",
            }
        except (
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.TimeoutException,
        ) as exc:
            logger.warning(
                f"Timeout awaiting response for item {processing_item_id}: {exc}"
            )
            return {"status": "OUTCOME_UNKNOWN", "error_code": "TIMEOUT"}
        except Exception as exc:
            logger.warning(
                f"Transport/protocol error during/after transmission for item {processing_item_id}: {exc}"
            )
            return {"status": "OUTCOME_UNKNOWN", "error_code": "TRANSPORT_ERROR"}

    def get_write_status(
        self, idempotency_key: str, correlation_id: str = "c-reconcile"
    ) -> Dict[str, Any]:
        """Calls GET /internal/writes/{idempotency_key} on Database Writer service."""
        url = f"{self.base_url}/internal/writes/{idempotency_key}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._headers(correlation_id))
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        st = self.map_response_status(data)
                        if st == "OUTCOME_UNKNOWN":
                            return {
                                "status": "OUTCOME_UNKNOWN",
                                "error_code": "INVALID_RESPONSE_SCHEMA",
                            }
                        return data
                    except Exception:
                        return {
                            "status": "OUTCOME_UNKNOWN",
                            "error_code": "MALFORMED_JSON_RESPONSE",
                        }
                elif resp.status_code == 404:
                    return {"status": "NOT_FOUND"}
                else:
                    return {
                        "status": "OUTCOME_UNKNOWN",
                        "error_code": f"HTTP_{resp.status_code}",
                    }
        except Exception as exc:
            logger.warning(
                f"Error checking write status for key {idempotency_key}: {exc}"
            )
            return {"status": "OUTCOME_UNKNOWN", "error_code": "CONNECTION_ERROR"}

    def list_enterprises(self, correlation_id: str) -> list[dict[str, str]]:
        """Reads the minimal enterprise choice projection from Database Writer."""
        url = f"{self.base_url}/internal/enterprises"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._headers(correlation_id))
            if response.status_code != 200:
                raise DBWriterClientError("ENTERPRISE_LIST_UNAVAILABLE")
            data = response.json()
            rows = data.get("enterprises") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise DBWriterClientError("INVALID_ENTERPRISE_LIST")
            result: list[dict[str, str]] = []
            for row in rows:
                if (
                    not isinstance(row, dict)
                    or not row.get("id")
                    or not row.get("display_name")
                ):
                    raise DBWriterClientError("INVALID_ENTERPRISE_LIST")
                result.append(
                    {"id": str(row["id"]), "display_name": str(row["display_name"])}
                )
            return result
        except DBWriterClientError:
            raise
        except Exception as exc:
            logger.warning(
                "Enterprise list request failed with sanitized transport error"
            )
            raise DBWriterClientError("ENTERPRISE_LIST_UNAVAILABLE") from exc
