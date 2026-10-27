"""HTTP record/replay for public provider data, with credentials removed on disk.

Cache identity ignores credentials: use separate cache directories for providers,
accounts or tenants whose responses depend on identity. Do not use this public
data cache for personal/account responses. Secret-bearing keys are removed from
identity, while response secrets are redacted rather than replayed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlencode, urlsplit, urlunsplit

import httpx

from app.data_paths import resolve_data_dir

CacheMode = Literal["record", "replay", "live"]
_REDACTED = "[REDACTED]"
_SECRET_NAMES = {
    "key",
    "apikey",
    "accesstoken",
    "authtoken",
    "bearertoken",
    "refreshtoken",
    "idtoken",
    "token",
    "apitoken",
    "xapitoken",
    "auth",
    "authentication",
    "credential",
    "credentials",
    "secret",
    "clientsecret",
    "password",
    "passwd",
    "pwd",
    "authorization",
    "proxyauthorization",
    "cookie",
    "setcookie",
    "session",
    "sessionid",
    "signature",
    "sig",
    "xapikey",
    "xauthtoken",
    "xaccesstoken",
    "xamzsecuritytoken",
}
_IGNORED_HEADERS = {"host", "content-length", "accept-encoding", "connection", "user-agent"}
_DROPPED_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding"}
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_ASSIGN_RE = re.compile(r"(?P<key>[\w.-]+)(?P<sep>\s*[:=]\s*)(?P<value>[^\s&;,<>]+)")
_XML_SECRET_RE = re.compile(r"<(?P<key>[\w:.-]+)(?:\s[^>]*)?>(?P<value>[^<]*)</(?P=key)>")
_CHARSET_RE = re.compile(r"(?P<prefix>(?:^|;)\s*charset\s*=\s*)[\"']?(?P<encoding>[\w-]+)[\"']?", re.IGNORECASE)


class CacheError(RuntimeError):
    """Base error; messages never contain provider payloads or credentialed URLs."""


class CacheMiss(CacheError):
    pass


class CacheFormatError(CacheError):
    pass


class CacheSafetyError(CacheError):
    pass


class ProviderRequestError(CacheError):
    pass


def _is_secret(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    return normalized in _SECRET_NAMES or normalized.endswith(("apikey", "token", "secret", "password"))


def _remember(value: Any, secrets: set[str]) -> None:
    if isinstance(value, Mapping):
        for child in value.values():
            _remember(child, secrets)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _remember(child, secrets)
    elif value is not None:
        text = str(value)
        if text:
            secrets.update((text, quote(text, safe=""), quote_plus(text)))
            if len(text) >= 2 and text[0] in {"'", '"'} and text[-1] == text[0]:
                secrets.add(text[1:-1])
            # Response diagnostics often echo only the credential after Bearer/Basic.
            if text.startswith(("Bearer ", "Basic ")):
                secrets.add(text.split(" ", 1)[1])
            if text.startswith("Basic "):
                try:
                    decoded = base64.b64decode(text.split(" ", 1)[1], validate=True).decode("utf-8")
                    for part in decoded.split(":", 1):
                        if part:
                            secrets.add(part)
                except (ValueError, UnicodeError):
                    pass
    secrets.discard("")


def _remember_header(name: str, value: str, secrets: set[str]) -> None:
    _remember(value, secrets)
    if name.casefold() in {"cookie", "set-cookie"}:
        try:
            cookies = SimpleCookie()
            cookies.load(value)
            for cookie in cookies.values():
                _remember(cookie.value, secrets)
        except CookieError:
            pass


def _safe_url(value: str, secrets: set[str]) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if parts.scheme not in {"http", "https"} or host is None:
            raise ValueError
        if parts.username is not None:
            _remember(unquote(parts.username), secrets)
        if parts.password is not None:
            _remember(unquote(parts.password), secrets)
        port = parts.port
        netloc = f"[{host.lower()}]" if ":" in host else host.lower()
        if port is not None and port != (443 if parts.scheme == "https" else 80):
            netloc += f":{port}"
        pairs = []
        for key, item in parse_qsl(parts.query, keep_blank_values=True):
            if _is_secret(key):
                _remember(item, secrets)
            else:
                pairs.append((key, item))
        # Stable key ordering, retaining the order of repeated values of one key.
        pairs.sort(key=lambda pair: pair[0])
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(pairs), ""))
    except (ValueError, TypeError):
        raise CacheSafetyError("Expected an absolute public HTTP(S) URL") from None


def _clean_text(value: str, secrets: set[str]) -> str:
    # First collect/remove URL credentials and secrets declared by diagnostic keys.
    value = _URL_RE.sub(lambda match: _safe_url(match.group(), secrets), value)

    def assignment(match: re.Match[str]) -> str:
        if not _is_secret(match["key"]):
            return match.group()
        _remember(match["value"], secrets)
        return match["key"] + match["sep"] + _REDACTED

    value = _ASSIGN_RE.sub(assignment, value)

    def xml_secret(match: re.Match[str]) -> str:
        if not _is_secret(match["key"].split(":")[-1]):
            return match.group()
        _remember(match["value"], secrets)
        return f"<{match['key']}>{_REDACTED}</{match['key']}>"

    value = _XML_SECRET_RE.sub(xml_secret, value)
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, _REDACTED)
    return value


def _clean_json(value: Any, secrets: set[str], *, identity: bool = False) -> Any:
    if isinstance(value, Mapping):
        # Collect every credential before redacting siblings that may echo it.
        for key, item in value.items():
            if _is_secret(str(key)):
                _remember(item, secrets)
        return {
            str(key): (_REDACTED if _is_secret(str(key)) else _clean_json(item, secrets, identity=identity))
            for key, item in value.items()
            if not (identity and _is_secret(str(key)))
        }
    if isinstance(value, (list, tuple)):
        return [_clean_json(item, secrets, identity=identity) for item in value]
    return _clean_text(value, secrets) if isinstance(value, str) else value


def _decode(body: bytes, content_type: str) -> tuple[str, str]:
    encoding = "utf-8"
    declared = _CHARSET_RE.search(content_type)
    xml = re.search(rb"<\?xml[^>]*encoding=[\"']([\w-]+)", body[:200], re.IGNORECASE)
    if declared:
        encoding = declared["encoding"]
    elif xml:
        encoding = xml[1].decode("ascii")
    try:
        return body.decode(encoding), encoding
    except (LookupError, UnicodeError):
        raise CacheSafetyError("Only decodable text/JSON/XML provider responses may be cached") from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Non-finite JSON values")


def _body(body: bytes, content_type: str, secrets: set[str], *, identity: bool = False) -> tuple[bytes, str | None]:
    """Return sanitized bytes and a replacement charset only when re-encoded."""
    if not body:
        return b"", None
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type and not (
        media_type.startswith("text/")
        or "json" in media_type
        or "xml" in media_type
        or media_type == "application/x-www-form-urlencoded"
    ):
        raise CacheSafetyError("Only text/JSON/XML responses and JSON/form requests may be cached")
    text, encoding = _decode(body, content_type)
    if "json" in content_type.casefold() or text.lstrip().startswith(("{", "[")):
        try:
            value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except ValueError:
            raise CacheSafetyError("Invalid JSON response cannot be safely recorded") from None
        # Two passes collect secrets from any branch before sanitizing echoes elsewhere.
        _clean_json(value, secrets, identity=identity)
        clean = _clean_json(value, secrets, identity=identity)
        return (
            json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            ),
            "utf-8",
        )
    if "application/x-www-form-urlencoded" in content_type.casefold():
        pairs = parse_qsl(text, keep_blank_values=True)
        for key, value in pairs:
            if _is_secret(key):
                _remember(value, secrets)
        return (
            urlencode(
                [
                    (key, _REDACTED if _is_secret(key) else _clean_text(value, secrets))
                    for key, value in sorted(pairs, key=lambda pair: pair[0])
                    if not (identity and _is_secret(key))
                ]
            ).encode("utf-8"),
            "utf-8",
        )
    if identity:
        raise CacheSafetyError("Request bodies must be JSON or form data for safe cache identity")
    return _clean_text(text, secrets).encode(encoding), None


def _request_identity(request: httpx.Request, secrets: set[str]) -> dict[str, Any]:
    url = _safe_url(str(request.url), secrets)
    for key, value in request.headers.multi_items():
        if _is_secret(key):
            _remember_header(key, value, secrets)
    body, _ = _body(request.content, request.headers.get("content-type", ""), secrets, identity=True)
    headers = [
        [key.lower(), _clean_text(value, secrets)]
        for key, value in request.headers.multi_items()
        if key.lower() not in _IGNORED_HEADERS and not _is_secret(key)
    ]
    # Re-sanitize after all body credentials are known.
    return {
        "method": request.method,
        "url": _clean_text(url, secrets),
        "headers": sorted(headers, key=lambda pair: pair[0]),
        "body": _clean_text(body.decode("utf-8"), secrets),
    }


def _digest(identity: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _safe_response(response: httpx.Response, identity: dict[str, Any], secrets: set[str]) -> httpx.Response:
    for key, value in response.headers.multi_items():
        if _is_secret(key):
            _remember_header(key, value, secrets)
        else:
            # A Location URL can introduce a credential also echoed in the body.
            _clean_text(value, secrets)
    body, charset = _body(response.content, response.headers.get("content-type", ""), secrets)
    headers = [
        (key, _clean_text(value, secrets))
        for key, value in response.headers.multi_items()
        if key.lower() not in _DROPPED_RESPONSE_HEADERS and not _is_secret(key)
    ]
    if charset is not None:
        headers = [
            (
                key,
                _CHARSET_RE.sub(lambda match: match["prefix"] + charset, value)
                if _CHARSET_RE.search(value)
                else value + "; charset=" + charset,
            )
            if key.lower() == "content-type"
            else (key, value)
            for key, value in headers
        ]
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=body,
        request=httpx.Request(identity["method"], identity["url"]),
    )


class CachedHttpClient:
    """An async, credential-redacting public-data client. Default mode is offline.

    A supplied AsyncClient is a transport; its default query/header settings are
    not merged into requests. Supply request-specific params/headers explicitly.
    Supplied clients are not closed by this wrapper. Redirects are not followed.
    """

    def __init__(
        self,
        *,
        mode: CacheMode = "replay",
        cache_dir: str | Path | None = None,
        client: httpx.AsyncClient | None = None,
        secret_values: Iterable[str] = (),
        timeout: float = 20,
    ) -> None:
        if mode not in {"record", "replay", "live"}:
            raise ValueError("Cache mode must be record, replay or live")
        self.mode = mode
        self.cache_dir = Path(cache_dir) if cache_dir is not None else resolve_data_dir() / "cache"
        self._client = client
        self._owns_client = client is None
        self._secret_values = tuple(secret_values)
        self._timeout = timeout
        self._closed = False

    async def __aenter__(self) -> CachedHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._closed = True

    async def close(self) -> None:
        await self.aclose()

    async def get(self, url: str | httpx.URL, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def request(
        self,
        method: str,
        url: str | httpx.URL,
        *,
        params: Any = None,
        headers: Any = None,
        json: Any = None,
        data: Any = None,
        content: Any = None,
        timeout: float | None = None,  # noqa: ASYNC109 - mirrors the HTTPX request timeout API.
    ) -> httpx.Response:
        if self._closed:
            raise CacheError("HTTP cache client is closed")
        secrets: set[str] = set()
        for value in self._secret_values:
            _remember(value, secrets)
        try:
            request = httpx.Request(method, url, params=params, headers=headers, json=json, data=data, content=content)
            identity = _request_identity(request, secrets)
        except CacheError:
            raise
        except Exception:
            raise CacheSafetyError("Cannot construct safe public-data request") from None
        key = _digest(identity)
        if self.mode == "replay":
            return await asyncio.to_thread(self._replay, key, identity, secrets)
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        try:
            deadline = self._timeout if timeout is None else timeout
            request.extensions["timeout"] = {part: deadline for part in ("connect", "read", "write", "pool")}
            response = await self._client.send(request, auth=None, follow_redirects=False)
            safe = _safe_response(response, identity, secrets)
        except CacheError:
            raise
        except Exception:
            raise ProviderRequestError("Public provider request failed; credentials and payload omitted") from None
        if self.mode == "record":
            await asyncio.to_thread(self._record, key, identity, safe)
        safe.extensions.update(cache_mode=self.mode, cache_hit=False)
        return safe

    def _record(self, key: str, identity: dict[str, Any], response: httpx.Response) -> None:
        entry = {
            "schema_version": 1,
            "recorded_at": datetime.now(UTC).isoformat(),
            "request": identity,
            "response": {
                "status_code": response.status_code,
                "headers": list(response.headers.multi_items()),
                "body_base64": base64.b64encode(response.content).decode("ascii"),
                "body_sha256": hashlib.sha256(response.content).hexdigest(),
            },
        }
        temporary: str | None = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_dir, delete=False) as stream:
                temporary = stream.name
                json.dump(entry, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                # Sanitized public fixtures must remain readable after Docker
                # COPY changes ownership to root and the app runs as non-root.
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, self.cache_dir / f"{key}.json")
        except OSError:
            raise CacheError("Cannot atomically write HTTP cache") from None
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def _replay(self, key: str, identity: dict[str, Any], secrets: set[str]) -> httpx.Response:
        try:
            raw = (self.cache_dir / f"{key}.json").read_text(encoding="utf-8")
        except FileNotFoundError:
            raise CacheMiss(f"No recorded public response: {key}") from None
        except (OSError, UnicodeError):
            raise CacheFormatError("Cannot read HTTP cache entry") from None
        try:
            entry = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
            if type(entry["schema_version"]) is not int or entry["schema_version"] != 1 or entry["request"] != identity:
                raise ValueError
            timestamp = datetime.fromisoformat(entry["recorded_at"])
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError
            recorded = entry["response"]
            body = base64.b64decode(recorded["body_base64"], validate=True)
            status = recorded["status_code"]
            if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
                raise ValueError
            if hashlib.sha256(body).hexdigest() != recorded["body_sha256"]:
                raise ValueError
            response = httpx.Response(status, headers=recorded["headers"], content=body)
            safe = _safe_response(response, identity, secrets)
            safe.extensions.update(cache_mode="replay", cache_hit=True, recorded_at=entry["recorded_at"])
            return safe
        except Exception:
            raise CacheFormatError("Invalid or corrupted HTTP cache entry") from None
