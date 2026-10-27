# ruff: noqa: ASYNC240 - fixture file reads are small, local and deliberately synchronous.

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.tools.http_cache import (
    CachedHttpClient,
    CacheError,
    CacheFormatError,
    CacheMiss,
    CacheSafetyError,
    ProviderRequestError,
)


def entry(path: Path) -> dict:
    return json.loads(next(path.glob("*.json")).read_text())


def cached_body(path: Path) -> bytes:
    return base64.b64decode(entry(path)["response"]["body_base64"])


async def test_record_replay_normalizes_query_order_and_omits_credentials(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["x-access-token"] == "private-provider-key"
        return httpx.Response(200, json={"flights": [{"price": 12345}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            first = await client.get(
                "https://PUBLIC.example:443/flights#fragment",
                params={"origin": "MOW", "destination": "DXB", "api_key": "private-query-key"},
                headers={"X-Access-Token": "private-provider-key"},
            )
        assert not transport.is_closed
        async with CachedHttpClient(cache_dir=tmp_path, client=transport) as replay:
            second = await replay.get("https://public.example/flights?destination=DXB&origin=MOW")
    assert first.json() == second.json() == {"flights": [{"price": 12345}]}
    assert len(calls) == 1
    assert first.extensions["cache_hit"] is False
    assert second.extensions["cache_hit"] is True
    assert "private-" not in next(tmp_path.glob("*.json")).read_text()


async def test_replay_miss_never_calls_network(tmp_path: Path) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("replay attempted a network call")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as transport:
        async with CachedHttpClient(cache_dir=tmp_path, client=transport) as client:
            with pytest.raises(CacheMiss, match="No recorded public response"):
                await client.get("https://public.example/missing?api_key=hidden")
    assert not list(tmp_path.iterdir())


async def test_live_returns_provider_data_without_reading_or_writing_cache(tmp_path: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    ) as t:
        async with CachedHttpClient(mode="live", cache_dir=tmp_path, client=t) as client:
            response = await client.get("https://public.example/")
    assert response.json() == {"ok": True}
    assert not list(tmp_path.iterdir())


async def test_nested_body_credentials_and_echoes_redacted_but_business_fields_survive(tmp_path: Path) -> None:
    body = {
        "origin": "MOW",
        "travelers": 2,
        "nested": [{"auth": {"api_key": "nested-request-secret"}}],
        "max_tokens": 100,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["nested"][0]["auth"]["api_key"] == "nested-request-secret"
        return httpx.Response(
            200,
            json={
                "message": "echo nested-request-secret and newly-issued-secret",
                "nested": [{"token": "newly-issued-secret", "price": 500}],
                "max_tokens": 100,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            response = await client.request("POST", "https://public.example/search", json=body)
    assert response.json()["nested"][0] == {"token": "[REDACTED]", "price": 500}
    assert response.json()["max_tokens"] == 100
    assert "secret" not in response.text
    assert "secret" not in cached_body(tmp_path).decode()
    clean = {"origin": "MOW", "travelers": 2, "nested": [{"auth": {}}], "max_tokens": 100}
    async with CachedHttpClient(cache_dir=tmp_path) as replay:
        assert (await replay.request("POST", "https://public.example/search", json=clean)).json() == response.json()


async def test_secret_urls_headers_text_and_status_errors_are_safe(tmp_path: Path) -> None:
    secrets = ("request-query-secret", "request-header-secret", "cookie-secret", "new-header-secret", "new-url-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            headers={
                "set-cookie": "session=new-header-secret; HttpOnly",
                "location": "https://public.example/login?token=new-url-secret&lang=ru",
                "content-type": "text/plain",
            },
            text="echo request-query-secret request-header-secret cookie-secret password=error-body-secret",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            response = await client.get(
                "https://public.example/search?api_key=request-query-secret&origin=MOW",
                headers={"Authorization": "Bearer request-header-secret", "Cookie": "sid=cookie-secret"},
            )
    assert "set-cookie" not in response.headers
    assert response.headers["location"] == "https://public.example/login?lang=ru"
    assert "error-body-secret" not in response.text
    with pytest.raises(httpx.HTTPStatusError) as failure:
        response.raise_for_status()
    combined = str(failure.value) + response.text + str(response.request.url) + cached_body(tmp_path).decode()
    assert all(secret not in combined for secret in secrets)
    assert "password=[REDACTED]" in response.text


async def test_transport_error_message_does_not_reveal_original_exception(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("provider echoed secret-transport-value in URL", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            with pytest.raises(ProviderRequestError) as error:
                await client.get("https://public.example/?token=secret-transport-value")
    assert "secret-transport-value" not in str(error.value)
    assert not list(tmp_path.iterdir())


async def test_normalization_preserves_repeated_value_order_and_business_headers(tmp_path: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    ) as t:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=t) as client:
            await client.get("https://public.example/?a=1&a=2", headers={"Accept-Language": "ru"})
    async with CachedHttpClient(cache_dir=tmp_path) as client:
        with pytest.raises(CacheMiss):
            await client.get("https://public.example/?a=2&a=1", headers={"Accept-Language": "ru"})
        with pytest.raises(CacheMiss):
            await client.get("https://public.example/?a=1&a=2", headers={"Accept-Language": "en"})


async def test_post_body_changes_do_not_reuse_other_search_results(tmp_path: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    ) as t:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=t) as client:
            await client.request("POST", "https://public.example/search", json={"origin": "MOW", "token": "hidden"})
    async with CachedHttpClient(cache_dir=tmp_path) as client:
        assert (
            await client.request("POST", "https://public.example/search", json={"origin": "MOW"})
        ).status_code == 200
        with pytest.raises(CacheMiss):
            await client.request("POST", "https://public.example/search", json={"origin": "LED"})


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json; charset=windows-1251; profile=public",
        'application/json; Charset = "windows-1251"; profile=public',
        "text/plain; charset=windows-1251; profile=public",
    ],
)
async def test_cp1251_json_rewrite_updates_charset_in_record_and_replay(tmp_path: Path, content_type: str) -> None:
    payload = {"city": "Москва", "token": "private-response-token"}
    original = json.dumps(payload, ensure_ascii=False).encode("cp1251")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=original, headers={"content-type": content_type})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            first = await client.get("https://public.example/cities")
        async with CachedHttpClient(cache_dir=tmp_path, client=transport) as client:
            second = await client.get("https://public.example/cities")

    assert len(calls) == 1
    expected = {"city": "Москва", "token": "[REDACTED]"}
    for response in (first, second):
        assert response.encoding == "utf-8"
        assert response.json() == json.loads(response.text) == expected
        assert "Москва" in response.text
        assert "private-response-token" not in response.text
        assert response.headers["content-type"].startswith(content_type.split(";", 1)[0] + ";")
        assert response.headers["content-type"].endswith("; profile=public")
    assert first.content == second.content == cached_body(tmp_path)
    assert first.headers["content-type"] == second.headers["content-type"]


async def test_cp1251_xml_is_preserved_byte_for_byte(tmp_path: Path) -> None:
    xml = '<?xml version="1.0" encoding="windows-1251"?><ValCurs Date="24.10.2026">Рубль</ValCurs>'.encode("cp1251")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=xml, headers={"content-type": "text/xml; charset=windows-1251"})
        )
    ) as t:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=t) as client:
            first = await client.get("https://www.cbr.ru/scripts/XML_daily.asp")
    async with CachedHttpClient(cache_dir=tmp_path) as client:
        second = await client.get("https://www.cbr.ru/scripts/XML_daily.asp")
    assert first.content == second.content == cached_body(tmp_path) == xml
    assert first.headers["content-type"] == second.headers["content-type"] == "text/xml; charset=windows-1251"
    assert "Рубль" in second.text


@pytest.mark.parametrize("corrupt", ["json", "version", "hash", "identity", "status", "base64", "date", "bool_version"])
async def test_corrupt_cache_errors_do_not_echo_payload(tmp_path: Path, corrupt: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    ) as t:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=t) as client:
            await client.get("https://public.example/")
    path = next(tmp_path.glob("*.json"))
    raw = entry(tmp_path)
    if corrupt == "json":
        path.write_text("unsafe-invalid-private-payload")
    else:
        if corrupt == "version":
            raw["schema_version"] = 2
        elif corrupt == "hash":
            raw["response"]["body_sha256"] = "incorrect"
        elif corrupt == "identity":
            raw["request"]["url"] = "https://private-url.example/"
        elif corrupt == "status":
            raw["response"]["status_code"] = True
        elif corrupt == "date":
            raw["recorded_at"] = "2026-10-26"
        elif corrupt == "bool_version":
            raw["schema_version"] = True
        else:
            raw["response"]["body_base64"] = "not valid base64 secret"
        path.write_text(json.dumps(raw))
    async with CachedHttpClient(cache_dir=tmp_path) as client:
        with pytest.raises(CacheFormatError) as failure:
            await client.get("https://public.example/")
    assert "private" not in str(failure.value)
    assert "secret" not in str(failure.value)


async def test_failed_atomic_write_keeps_old_record_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.tools import http_cache

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    ) as t:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=t) as client:
            await client.get("https://public.example/")
            old = next(tmp_path.iterdir()).read_bytes()

            def fails(*args: object) -> None:
                raise OSError("private-file-path")

            monkeypatch.setattr(http_cache.os, "replace", fails)
            with pytest.raises(CacheError, match="atomically"):
                await client.get("https://public.example/")
    assert len(list(tmp_path.iterdir())) == 1
    assert next(tmp_path.iterdir()).read_bytes() == old


@pytest.mark.parametrize("body", [b"\x89PNG\xff\xff", b"{bad json", b'{"price":NaN}', b'{"price":1,"price":2}'])
async def test_opaque_or_invalid_json_is_not_silently_persisted(tmp_path: Path, body: bytes) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))) as t:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=t) as client:
            with pytest.raises(CacheSafetyError):
                await client.get("https://public.example/")
    assert not list(tmp_path.iterdir())


async def test_raw_request_body_requires_json_or_form_identity(tmp_path: Path) -> None:
    async with CachedHttpClient(cache_dir=tmp_path) as client:
        with pytest.raises(CacheSafetyError, match="bodies must be JSON or form"):
            await client.request("POST", "https://public.example/", content=b"password value in opaque body")


async def test_form_secret_omission_supports_replay_without_credentials(tmp_path: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    ) as t:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=t) as client:
            await client.request("POST", "https://public.example/", data={"q": "sea", "api_key": "private-form-key"})
    async with CachedHttpClient(cache_dir=tmp_path) as client:
        response = await client.request("POST", "https://public.example/", data={"q": "sea"})
    assert response.json() == {"ok": True}


async def test_redirects_not_followed_implicitly(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://other.example/?token=private-redirect"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as t:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=t) as client:
            response = await client.get("https://public.example/")
    assert len(calls) == 1
    assert response.status_code == 302
    assert response.headers["location"] == "https://other.example/"


async def test_userinfo_and_explicit_secret_values_do_not_survive(tmp_path: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="private-user private-password private-explicit")
        )
    ) as t:
        async with CachedHttpClient(
            mode="record", cache_dir=tmp_path, client=t, secret_values=["private-explicit"]
        ) as client:
            response = await client.get("https://private-user:private-password@public.example/")
    assert "private-" not in response.text
    assert str(response.request.url) == "https://public.example/"
    assert "private-" not in cached_body(tmp_path).decode()


async def test_closed_client_fails_before_network(tmp_path: Path) -> None:
    client = CachedHttpClient(cache_dir=tmp_path)
    await client.close()
    with pytest.raises(CacheError, match="closed"):
        await client.get("https://public.example/")


async def test_new_secret_in_redirect_header_redacts_its_body_echo(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://public.example/?apiToken=new-url-credential"},
            json={"message": "new-url-credential"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            response = await client.get("https://public.example/")
    assert response.json() == {"message": "[REDACTED]"}
    assert "credential" not in cached_body(tmp_path).decode()


async def test_injected_transport_default_auth_is_not_implicitly_sent(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), auth=("private-user", "private-password")
    ) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            assert (await client.get("https://public.example/")).status_code == 200


async def test_cookie_attributes_are_not_treated_as_secret_values(tmp_path: Path) -> None:
    xml = b'<?xml version="1.0"?><root><value>1</value></root>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=xml,
            headers={
                "content-type": "application/xml",
                "set-cookie": "provider=private-cookie; Path=/; SameSite=Lax; Max-Age=3600; HttpOnly",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            response = await client.get("https://public.example/")
    assert response.content == xml
    assert response.headers["content-type"] == "application/xml"
    assert "set-cookie" not in response.headers


async def test_percent_encoded_userinfo_and_basic_auth_echo_are_redacted(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "private@user private:password"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            response = await client.get("https://private%40user:private%3Apassword@public.example/")
    assert response.json() == {"message": "[REDACTED] [REDACTED]"}
    assert "private" not in cached_body(tmp_path).decode()
    assert next(tmp_path.glob("*.json")).stat().st_mode & 0o777 == 0o644


async def test_declared_binary_media_type_rejected_even_if_bytes_look_like_text(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"ASCII is not evidence this is safe text", headers={"content-type": "image/png"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            with pytest.raises(CacheSafetyError):
                await client.get("https://public.example/")


async def test_repeated_header_order_is_preserved_in_identity(tmp_path: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    ) as transport:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=transport) as client:
            await client.get(
                "https://public.example/", headers=[("Accept", "application/json"), ("Accept", "text/xml")]
            )
    async with CachedHttpClient(cache_dir=tmp_path) as client:
        with pytest.raises(CacheMiss):
            await client.get(
                "https://public.example/", headers=[("Accept", "text/xml"), ("Accept", "application/json")]
            )


def test_invalid_mode_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mode"):
        CachedHttpClient(mode="sometimes", cache_dir=tmp_path)
