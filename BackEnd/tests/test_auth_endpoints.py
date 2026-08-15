import bcrypt
import pytest
import pytest_asyncio
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User
from routers import auth as auth_router


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def user_with_password(session_factory):
    password = "correct-horse-battery-staple"
    async with session_factory() as db:
        u = User(
            name="Ada",
            email="ada@example.com",
            password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id, password


class FakeClient:
    def __init__(self, host):
        self.host = host


class FakeURL:
    def __init__(self, path):
        self.path = path


class FakeRequest:
    def __init__(self, cookies=None, host="127.0.0.1", path="/api/auth/token"):
        self.cookies = cookies or {}
        self.client = FakeClient(host)
        self.url = FakeURL(path)


class FakeFormData:
    def __init__(self, username, password):
        self.username = username
        self.password = password


def _cookie_value(response, name):
    for key, value in response.raw_headers:
        if key == b"set-cookie" and value.decode().startswith(f"{name}="):
            return value.decode().split(";")[0].split("=", 1)[1]
    return None


@pytest.mark.asyncio
async def test_login_returns_access_token_and_sets_refresh_cookie(session_factory, user_with_password):
    _, password = user_with_password
    response = Response()

    async with session_factory() as db:
        result = await auth_router.login_for_access_token(
            response=response, form_data=FakeFormData("ada@example.com", password), db=db,
        )

    assert result["token_type"] == "bearer"
    assert result["access_token"]
    assert _cookie_value(response, "refresh_token") is not None


@pytest.mark.asyncio
async def test_login_rejects_wrong_password_with_401(session_factory, user_with_password):
    response = Response()
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await auth_router.login_for_access_token(
                response=response, form_data=FakeFormData("ada@example.com", "wrong-password"), db=db,
            )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_unknown_email_with_401(session_factory):
    response = Response()
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await auth_router.login_for_access_token(
                response=response, form_data=FakeFormData("nobody@example.com", "whatever"), db=db,
            )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_login_locks_account_after_max_failed_attempts(session_factory, user_with_password):
    for _ in range(auth_router.LOGIN_MAX_ATTEMPTS):
        response = Response()
        async with session_factory() as db:
            with pytest.raises(HTTPException) as exc_info:
                await auth_router.login_for_access_token(
                    response=response, form_data=FakeFormData("ada@example.com", "wrong-password"), db=db,
                )
            assert exc_info.value.status_code == 401

    response = Response()
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await auth_router.login_for_access_token(
                response=response, form_data=FakeFormData("ada@example.com", "wrong-password"), db=db,
            )
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_correct_password_still_locked_out_returns_429(session_factory, user_with_password):
    _, password = user_with_password
    for _ in range(auth_router.LOGIN_MAX_ATTEMPTS):
        response = Response()
        async with session_factory() as db:
            with pytest.raises(HTTPException):
                await auth_router.login_for_access_token(
                    response=response, form_data=FakeFormData("ada@example.com", "wrong-password"), db=db,
                )

    response = Response()
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await auth_router.login_for_access_token(
                response=response, form_data=FakeFormData("ada@example.com", password), db=db,
            )
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_refresh_rotates_the_cookie_and_returns_a_new_access_token(session_factory, user_with_password):
    _, password = user_with_password
    login_response = Response()
    async with session_factory() as db:
        await auth_router.login_for_access_token(
            response=login_response, form_data=FakeFormData("ada@example.com", password), db=db,
        )
    raw_refresh_token = _cookie_value(login_response, "refresh_token")

    refresh_response = Response()
    async with session_factory() as db:
        result = await auth_router.refresh_access_token(
            request=FakeRequest(cookies={"refresh_token": raw_refresh_token}, path="/api/auth/refresh"),
            response=refresh_response,
            db=db,
        )

    assert result["access_token"]
    new_raw_refresh_token = _cookie_value(refresh_response, "refresh_token")
    assert new_raw_refresh_token is not None
    assert new_raw_refresh_token != raw_refresh_token


@pytest.mark.asyncio
async def test_refresh_with_no_cookie_401s(session_factory):
    # refresh_access_token returns a JSONResponse (rather than raising
    # HTTPException) on every failure branch, since FastAPI/Starlette
    # discards a Response parameter's header mutations when an endpoint
    # raises — see the note in routers/auth.py's refresh_access_token.
    async with session_factory() as db:
        result = await auth_router.refresh_access_token(
            request=FakeRequest(cookies={}, path="/api/auth/refresh"), response=Response(), db=db,
        )
    assert result.status_code == 401


@pytest.mark.asyncio
async def test_replaying_a_rotated_refresh_token_401s_and_clears_the_cookie(session_factory, user_with_password):
    _, password = user_with_password
    login_response = Response()
    async with session_factory() as db:
        await auth_router.login_for_access_token(
            response=login_response, form_data=FakeFormData("ada@example.com", password), db=db,
        )
    raw_refresh_token = _cookie_value(login_response, "refresh_token")

    async with session_factory() as db:
        await auth_router.refresh_access_token(
            request=FakeRequest(cookies={"refresh_token": raw_refresh_token}, path="/api/auth/refresh"),
            response=Response(), db=db,
        )

    async with session_factory() as db:
        replay_result = await auth_router.refresh_access_token(
            request=FakeRequest(cookies={"refresh_token": raw_refresh_token}, path="/api/auth/refresh"),
            response=Response(), db=db,
        )
    assert replay_result.status_code == 401
    assert any(key == b"set-cookie" for key, _ in replay_result.raw_headers)


@pytest.mark.asyncio
async def test_logout_revokes_the_refresh_token_and_clears_the_cookie(session_factory, user_with_password):
    _, password = user_with_password
    login_response = Response()
    async with session_factory() as db:
        await auth_router.login_for_access_token(
            response=login_response, form_data=FakeFormData("ada@example.com", password), db=db,
        )
    raw_refresh_token = _cookie_value(login_response, "refresh_token")

    logout_response = Response()
    async with session_factory() as db:
        await auth_router.logout(
            request=FakeRequest(cookies={"refresh_token": raw_refresh_token}, path="/api/auth/logout"),
            response=logout_response, db=db,
        )
    assert any(key == b"set-cookie" for key, _ in logout_response.raw_headers)

    async with session_factory() as db:
        result = await auth_router.refresh_access_token(
            request=FakeRequest(cookies={"refresh_token": raw_refresh_token}, path="/api/auth/refresh"),
            response=Response(), db=db,
        )
    assert result.status_code == 401


def test_ip_rate_limiter_allows_requests_up_to_the_limit():
    auth_router._login_ip_cache.clear()
    request = FakeRequest(host="10.0.0.5", path="/api/auth/token")
    for _ in range(auth_router.LOGIN_IP_LIMIT_PER_MINUTE):
        auth_router._check_login_ip_rate_limit(request)  # must not raise


def test_ip_rate_limiter_blocks_requests_over_the_limit():
    auth_router._login_ip_cache.clear()
    request = FakeRequest(host="10.0.0.6", path="/api/auth/token")
    for _ in range(auth_router.LOGIN_IP_LIMIT_PER_MINUTE):
        auth_router._check_login_ip_rate_limit(request)
    with pytest.raises(HTTPException) as exc_info:
        auth_router._check_login_ip_rate_limit(request)
    assert exc_info.value.status_code == 429
