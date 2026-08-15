"""
Real ASGI-level integration tests for the auth-hardening feature.

tests/test_auth_endpoints.py exercises routers/auth.py's endpoint functions
directly via hand-rolled FakeRequest/FakeClient/FakeFormData objects and
direct coroutine calls, bypassing the real ASGI stack entirely. That's how
finding I1 (refresh_access_token's cookie-clear-on-raise being dead code)
slipped through: raising HTTPException after mutating a Response object
works fine when you inspect that same Python object afterward, but
FastAPI/Starlette discards those header mutations when the endpoint
actually raises through the real stack.

This file instead drives the real `app` from app.py over real HTTP via
httpx.AsyncClient(transport=ASGITransport(...)), so it exercises:
  - the actual Set-Cookie header FastAPI/Starlette put on the wire
    (attributes: HttpOnly/Secure/SameSite/Path), not a Python object's
    .raw_headers inspected out-of-band: to serialize
  - the real Depends(_check_login_ip_rate_limit) wiring on the routes,
    rather than calling _check_login_ip_rate_limit directly
  - the real exception-handling behavior fixed for finding I1

Deliberately NOT a full re-coverage of test_auth_endpoints.py's unit-level
scenarios (lockout thresholds, reuse-detection internals, etc.) — those
stay covered there and in test_refresh_token_service.py.
"""
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app import app
from database.connection import get_db
from database.models import Base, RefreshToken
from routers import auth as auth_router
from services.auth_service import COOKIE_SECURE


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory):
    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db
    auth_router._login_ip_cache.clear()
    try:
        # base_url is https:// (not http://) deliberately: COOKIE_SECURE
        # defaults to true, so the refresh cookie carries the Secure
        # attribute, and httpx's cookie jar (like a real browser) won't
        # re-send a Secure cookie on a subsequent request over a plain
        # http:// origin — using https:// here lets the client's cookie
        # jar behave the same way a real browser would against this app's
        # actual (HTTPS-fronted) deployment.
        transport = ASGITransport(app=app, client=("198.51.100.10", 12345))
        async with AsyncClient(transport=transport, base_url="https://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)
        auth_router._login_ip_cache.clear()


async def _register_and_login(client, email="ada@example.com", password="correct-horse-battery-staple"):
    register_resp = await client.post(
        "/api/auth/", json={"name": "Ada", "email": email, "password": password}
    )
    assert register_resp.status_code == 200, register_resp.text
    login_resp = await client.post(
        "/api/auth/token", data={"username": email, "password": password}
    )
    assert login_resp.status_code == 200, login_resp.text
    return login_resp


@pytest.mark.asyncio
async def test_login_sets_refresh_cookie_with_expected_attributes(client):
    login_resp = await _register_and_login(client)

    set_cookie_headers = login_resp.headers.get_list("set-cookie")
    refresh_cookie_header = next(h for h in set_cookie_headers if h.startswith("refresh_token="))

    assert "httponly" in refresh_cookie_header.lower()
    assert "path=/api/auth" in refresh_cookie_header.lower()
    assert "samesite=lax" in refresh_cookie_header.lower()
    # Secure presence must match whatever COOKIE_SECURE actually resolves to
    # in this test environment, rather than assuming either way.
    if COOKIE_SECURE:
        assert "secure" in refresh_cookie_header.lower()
    else:
        assert "secure" not in refresh_cookie_header.lower()

    assert login_resp.json()["access_token"]


@pytest.mark.asyncio
async def test_refresh_with_valid_cookie_returns_200_and_rotates_cookie(client):
    login_resp = await _register_and_login(client)
    old_refresh_cookie = client.cookies.get("refresh_token")
    assert old_refresh_cookie is not None

    refresh_resp = await client.post("/api/auth/refresh")

    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["access_token"]
    new_refresh_cookie = client.cookies.get("refresh_token")
    assert new_refresh_cookie is not None
    assert new_refresh_cookie != old_refresh_cookie


@pytest.mark.asyncio
async def test_replaying_an_already_rotated_refresh_cookie_401s_over_real_http(client, session_factory):
    await _register_and_login(client)
    original_refresh_cookie = client.cookies.get("refresh_token")

    # First refresh succeeds and rotates the cookie.
    first_refresh = await client.post("/api/auth/refresh")
    assert first_refresh.status_code == 200

    # Backdate the now-rotated-away record's revoked_at so the replay below
    # deterministically exercises the "old replay" reuse-detected path
    # rather than racing the C1 grace window in a fast-running test — from
    # the outside both paths look like a 401, which is all this endpoint-
    # level integration test asserts; C1's own unit tests in
    # test_refresh_token_service.py cover which internal branch fires.
    async with session_factory() as db:
        token_hash = auth_router.refresh_token_service._hash_token(original_refresh_cookie)
        result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        record = result.scalars().first()
        record.revoked_at = datetime.utcnow() - timedelta(seconds=60)
        await db.commit()

    # Replay the original (now long-revoked) cookie directly — set it back
    # onto the client's own cookie jar (it currently holds the rotated
    # successor from first_refresh) rather than using httpx's deprecated
    # per-request `cookies=` kwarg.
    client.cookies.set("refresh_token", original_refresh_cookie, domain="test.local", path="/api/auth")
    replay_resp = await client.post("/api/auth/refresh")
    assert replay_resp.status_code == 401
    # The cookie-clear from finding I1's fix must actually reach the wire.
    set_cookie_headers = replay_resp.headers.get_list("set-cookie")
    assert any(h.startswith("refresh_token=") for h in set_cookie_headers)


@pytest.mark.asyncio
async def test_logout_returns_200_and_clears_the_cookie_over_real_http(client):
    await _register_and_login(client)

    logout_resp = await client.post("/api/auth/logout")
    assert logout_resp.status_code == 200

    set_cookie_headers = logout_resp.headers.get_list("set-cookie")
    refresh_cookie_header = next(h for h in set_cookie_headers if h.startswith("refresh_token="))
    lowered = refresh_cookie_header.lower()
    assert "max-age=0" in lowered or refresh_cookie_header.startswith("refresh_token=;") or refresh_cookie_header.startswith('refresh_token="";')

    # A subsequent refresh with the (now-revoked) cookie the client still
    # holds locally must fail.
    refresh_after_logout = await client.post("/api/auth/refresh")
    assert refresh_after_logout.status_code == 401


@pytest.mark.asyncio
async def test_login_ip_throttle_returns_429_through_the_real_dependency_wiring(client):
    # Drives requests through the real route (not calling
    # _check_login_ip_rate_limit directly) enough times to trip the
    # per-IP throttle, confirming Depends(_check_login_ip_rate_limit) is
    # actually wired into POST /api/auth/token over the real ASGI stack.
    for _ in range(auth_router.LOGIN_IP_LIMIT_PER_MINUTE):
        resp = await client.post(
            "/api/auth/token", data={"username": "nobody@example.com", "password": "wrong"}
        )
        assert resp.status_code in (401, 429)

    throttled_resp = await client.post(
        "/api/auth/token", data={"username": "nobody@example.com", "password": "wrong"}
    )
    assert throttled_resp.status_code == 429


@pytest.mark.asyncio
async def test_refresh_with_no_cookie_401s_over_real_http(client):
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401
