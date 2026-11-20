"""Minimal server-side adapter for the Tabrica Public REST API.

The Bearer token must only be available to the Python server. Never expose it
in portal.html, browser JavaScript, Git, screenshots or support messages.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class TabricaError(RuntimeError):
    pass


class TabricaStorage:
    api_url = "https://api.tabrica.ru/public/v1"

    def __init__(self) -> None:
        self.token = os.environ.get("TABRICA_API_TOKEN", "")
        self.database_id = self._positive_int("TABRICA_DATABASE_ID")
        self.tables = {
            "users": self._positive_int("TABRICA_USERS_TABLE_ID"),
            "sessions": self._positive_int("TABRICA_SESSIONS_TABLE_ID"),
            "applications": self._positive_int("TABRICA_APPLICATIONS_TABLE_ID"),
        }
        if not self.token:
            raise TabricaError("TABRICA_API_TOKEN is required")

    @staticmethod
    def _positive_int(name: str) -> int:
        try:
            value = int(os.environ.get(name, ""))
        except ValueError as exc:
            raise TabricaError(f"{name} must be an integer") from exc
        if value < 1:
            raise TabricaError(f"{name} is required")
        return value

    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        body = json.dumps({"data": data}, ensure_ascii=False).encode("utf-8") if data is not None else None
        request = urllib.request.Request(
            self.api_url + path,
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise TabricaError(f"Tabrica API returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TabricaError(f"Tabrica API is unavailable: {exc}") from exc
        if not isinstance(payload, dict):
            raise TabricaError("Tabrica API returned an unexpected response")
        return payload

    def _collection_path(self, table: str) -> str:
        return f"/databases/{self.database_id}/tables/{self.tables[table]}/records"

    @staticmethod
    def _record(raw: dict) -> dict:
        fields = raw.get("data") if isinstance(raw.get("data"), dict) else raw.copy()
        row_id = raw.get("row_id", raw.get("id", fields.get("row_id", fields.get("id"))))
        fields = dict(fields)
        fields["id"] = int(row_id)
        return fields

    def create(self, table: str, values: dict) -> dict:
        payload = self._request("POST", self._collection_path(table), values)
        data = payload.get("data", {})
        raw = data.get("record") if isinstance(data, dict) and isinstance(data.get("record"), dict) else data
        if not isinstance(raw, dict):
            raise TabricaError("Tabrica did not return the created record")
        if "id" not in raw and "row_id" not in raw and isinstance(data, dict) and "id" in data:
            raw = {**raw, "id": data["id"]}
        return self._record(raw)

    def list(self, table: str, *, where: str | None = None, limit: int = 100, sort: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100)), "offset": 0, "response_format": "names"}
        if where:
            params["where"] = where
        if sort:
            params["sort"] = sort
        payload = self._request("GET", self._collection_path(table) + "?" + urllib.parse.urlencode(params))
        data = payload.get("data", {})
        records = data.get("records", []) if isinstance(data, dict) else []
        if not isinstance(records, list):
            raise TabricaError("Tabrica did not return a record list")
        return [self._record(x) for x in records if isinstance(x, dict)]

    def first(self, table: str, field: str, value: str | int) -> dict | None:
        # Values used by the portal are normalized and do not contain the filter separators.
        safe = str(value).replace(",", " ").replace("(", " ").replace(")", " ").replace("~", " ")
        rows = self.list(table, where=f"({field},eq,{safe})", limit=1)
        return rows[0] if rows else None

    def get(self, table: str, row_id: int) -> dict:
        payload = self._request("GET", self._collection_path(table) + f"/{int(row_id)}?response_format=names")
        data = payload.get("data", {})
        raw = data.get("record") if isinstance(data, dict) and isinstance(data.get("record"), dict) else data
        if not isinstance(raw, dict):
            raise TabricaError("Tabrica did not return the requested record")
        return self._record(raw)

    def delete(self, table: str, row_id: int) -> None:
        self._request("DELETE", self._collection_path(table) + f"/{int(row_id)}")

    def create_user(self, values: dict) -> dict:
        return self.create("users", values)

    def user_by_email(self, email: str) -> dict | None:
        return self.first("users", "email", email)

    def user_by_id(self, user_id: int) -> dict | None:
        try:
            return self.get("users", user_id)
        except TabricaError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def create_session(self, values: dict) -> dict:
        return self.create("sessions", values)

    def session_by_hash(self, token_hash: str) -> dict | None:
        return self.first("sessions", "token_hash", token_hash)

    def delete_session(self, token_hash: str) -> None:
        session = self.session_by_hash(token_hash)
        if session:
            self.delete("sessions", session["id"])

    def create_application(self, values: dict) -> dict:
        return self.create("applications", values)

    def applications_for_user(self, user_id: int) -> list[dict]:
        return self.list("applications", where=f"(user_id,eq,{int(user_id)})", limit=100, sort="-created_at")

    def healthcheck(self) -> None:
        self.list("users", limit=1)
