# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import base64
import hashlib
import time
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

BASE_WEB = "https://www.sunsynk.net"
BASE_API = "https://api.sunsynk.net"
SOURCE = "sunsynk"
CLIENT_ID = "csp-web"


class SunsynkApiError(Exception):
    """Raised when the Sunsynk API returns an error."""


class SunsynkApiClient:
    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._token: str | None = None

    @staticmethod
    def _md5_hex(value: str) -> str:
        return hashlib.md5(value.encode("utf-8")).hexdigest()

    async def _json_or_raise(self, response: aiohttp.ClientResponse) -> dict[str, Any]:
        response.raise_for_status()
        data = await response.json()
        if isinstance(data, dict) and data.get("code", 0) not in (0, "0", None):
            raise SunsynkApiError(str(data))
        return data

    async def _get_public_key(self) -> str:
        nonce = int(time.time() * 1000)
        raw = f"nonce={nonce}&source={SOURCE}"
        sign = self._md5_hex(raw + "POWER_VIEW")
        async with self._session.get(
            f"{BASE_API}/anonymous/publicKey",
            params={"nonce": nonce, "source": SOURCE, "sign": sign},
            headers={"accept": "application/json", "origin": BASE_WEB, "referer": f"{BASE_WEB}/"},
        ) as response:
            body = await self._json_or_raise(response)
            public_key = body.get("data")
            if not public_key:
                raise SunsynkApiError(f"Public key missing in response: {body}")
            return public_key

    @staticmethod
    def _normalize_public_key(public_key: str) -> bytes:
        if "BEGIN PUBLIC KEY" not in public_key:
            public_key = f"-----BEGIN PUBLIC KEY-----\n{public_key}\n-----END PUBLIC KEY-----"
        return public_key.encode("utf-8")

    async def _encrypt_password(self, password: str, public_key: str) -> str:
        key = serialization.load_pem_public_key(self._normalize_public_key(public_key))
        encrypted = key.encrypt(password.encode("utf-8"), padding.PKCS1v15())
        return base64.b64encode(encrypted).decode("utf-8")

    async def async_login(self) -> None:
        public_key = await self._get_public_key()
        nonce = int(time.time() * 1000)
        raw = f"nonce={nonce}&source={SOURCE}"
        sign = self._md5_hex(raw + public_key[:10])
        encrypted_password = await self._encrypt_password(self._password, public_key)
        payload = {
            "sign": sign,
            "nonce": nonce,
            "username": self._username,
            "password": encrypted_password,
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "source": SOURCE,
        }
        async with self._session.post(
            f"{BASE_API}/oauth/token/new",
            json=payload,
            headers={
                "accept": "application/json",
                "content-type": "application/json;charset=UTF-8",
                "origin": BASE_WEB,
                "referer": f"{BASE_WEB}/",
            },
        ) as response:
            body = await self._json_or_raise(response)
            token = body.get("data", {}).get("access_token")
            if not token:
                raise SunsynkApiError(f"Access token missing in response: {body}")
            self._token = token

    async def _ensure_login(self) -> None:
        if not self._token:
            await self.async_login()

    async def async_post_income(self, plant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_login()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "accept": "application/json",
            "content-type": "application/json;charset=UTF-8",
            "origin": BASE_WEB,
            "referer": f"{BASE_WEB}/",
        }
        async with self._session.post(
            f"{BASE_API}/api/v1/plant/{plant_id}/income", json=payload, headers=headers
        ) as response:
            if response.status == 401:
                self._token = None
                await self.async_login()
                headers["Authorization"] = f"Bearer {self._token}"
                async with self._session.post(
                    f"{BASE_API}/api/v1/plant/{plant_id}/income", json=payload, headers=headers
                ) as retry_response:
                    return await self._json_or_raise(retry_response)
            return await self._json_or_raise(response)
