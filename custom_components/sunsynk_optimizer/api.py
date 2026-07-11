# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Sunsynk cloud API client — authentication, encryption, and income/Flux payload writes."""

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
SOURCE = "sunsynk"      # required literal value in all Sunsynk API requests
CLIENT_ID = "csp-web"  # required OAuth client identifier for the web portal flow


class SunsynkApiError(Exception):
    """Raised when the Sunsynk API returns an error."""


class SunsynkApiClient:
    """Thin async client for the Sunsynk cloud API.

    Login flow: fetch RSA public key → encrypt password with PKCS1v15 → POST credentials
    to obtain a bearer token. The token is reused across calls; a 401 response triggers
    a single re-login and retry.
    """

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._token: str | None = None

    @staticmethod
    def _md5_hex(value: str) -> str:
        """Return lowercase MD5 hex digest — used to sign API nonces."""
        return hashlib.md5(value.encode("utf-8")).hexdigest()

    async def _json_or_raise(self, response: aiohttp.ClientResponse) -> dict[str, Any]:
        """Raise on HTTP error or non-zero Sunsynk API code, otherwise return parsed JSON."""
        response.raise_for_status()
        data = await response.json()
        if isinstance(data, dict) and data.get("code", 0) not in (0, "0", None):
            raise SunsynkApiError(str(data))
        return data

    async def _get_public_key(self) -> str:
        """Fetch the RSA public key used to encrypt the login password.

        The endpoint requires a millisecond-precision nonce signed with MD5 using the
        literal string 'POWER_VIEW' as the signing secret.
        """
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
        """Wrap a bare base64 key string in PEM headers if the API omitted them."""
        if "BEGIN PUBLIC KEY" not in public_key:
            public_key = f"-----BEGIN PUBLIC KEY-----\n{public_key}\n-----END PUBLIC KEY-----"
        return public_key.encode("utf-8")

    async def _encrypt_password(self, password: str, public_key: str) -> str:
        """RSA-encrypt the password with PKCS1v15 padding, return base64 string.

        PKCS1v15 is required by the Sunsynk API — OAEP is not accepted.
        """
        key = serialization.load_pem_public_key(self._normalize_public_key(public_key))
        encrypted = key.encrypt(password.encode("utf-8"), padding.PKCS1v15())
        return base64.b64encode(encrypted).decode("utf-8")

    async def async_login(self) -> None:
        """Authenticate with the Sunsynk API and store the bearer token.

        The login nonce is signed with MD5 using the first 10 characters of the public key
        as the secret — this is the signing contract the Sunsynk web portal uses.
        """
        public_key = await self._get_public_key()
        nonce = int(time.time() * 1000)
        raw = f"nonce={nonce}&source={SOURCE}"
        sign = self._md5_hex(raw + public_key[:10])  # first 10 chars of key = login signing secret
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
            data = body.get("data")
            token = data.get("access_token") if isinstance(data, dict) else None
            if not token:
                raise SunsynkApiError(f"Access token missing in response: {body}")
            self._token = token

    async def _ensure_login(self) -> None:
        """Log in only if no token is held yet (lazy authentication)."""
        if not self._token:
            await self.async_login()

    async def async_post_income(self, plant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST an income/Flux config payload to the Sunsynk API.

        On a 401 the token is cleared and a single re-login + retry is attempted,
        which handles session expiry without requiring the caller to manage tokens.
        """
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
                # Token expired mid-session — re-authenticate and retry once.
                self._token = None
                await self.async_login()
                headers["Authorization"] = f"Bearer {self._token}"
                async with self._session.post(
                    f"{BASE_API}/api/v1/plant/{plant_id}/income", json=payload, headers=headers
                ) as retry_response:
                    return await self._json_or_raise(retry_response)
            return await self._json_or_raise(response)
