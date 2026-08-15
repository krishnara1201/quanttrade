# Auth Hardening: Refresh Tokens, Login Lockout, Deployment Readiness — Design

## Summary

Today's auth (`services/auth_service.py`, `routers/auth.py`, `FrontEnd/src/state/AuthContext.jsx`) issues a single JWT access token good for 1 day, stored in `localStorage`, with no way to revoke it and no refresh mechanism — once issued, a token is valid until it expires no matter what happens server-side. There is also no login-specific brute-force protection (only a generic global 100 req/60s per-IP limiter across every route), and `POST /api/auth/token` returns HTTP 200 with `{"error": "..."}` on bad credentials instead of a 401.

This design adds short-lived access tokens backed by a rotating, revocable refresh token (httpOnly cookie), per-account + per-IP login throttling, and a handful of deployment-readiness fixes (fail-fast secret validation, env-configurable lifetimes). Target deployment: same-domain/reverse-proxied frontend+backend, public-facing with multiple real users (confirmed with the user — this is why refresh tokens go in an httpOnly cookie rather than response-body storage, and why `SameSite=Lax` is sufficient CSRF protection without a separate CSRF-token scheme).

## Token mechanism

**Access token** — unchanged JWT format/claims (`sub`, `user_id`, `exp`), signed the same way via `SECRET_KEY`/`ALGORITHM`. Expiry drops from 1 day to **15 minutes**, via new env var `ACCESS_TOKEN_EXPIRE_MINUTES` (default `15`), read in `routers/auth.py` the same `os.getenv`-with-fallback way `SECRET_KEY`/`ALGORITHM` already are. `get_current_user` (`services/auth_service.py`) is otherwise unchanged — it already re-validates signature + expiry on every request.

**Refresh token** — a new opaque random token, `secrets.token_urlsafe(32)`, **not** a JWT. Only the SHA-256 hash of it is ever persisted (`hashlib.sha256(token.encode()).hexdigest()`), so a DB compromise doesn't hand out directly-usable tokens. New table:

```python
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
```

Lifetime **7 days**, via new env var `REFRESH_TOKEN_EXPIRE_DAYS` (default `7`). Delivered to the browser as an **httpOnly, Secure, SameSite=Lax** cookie named `refresh_token`, `path=/api/auth` (so it's never attached to non-auth requests), `max_age` matching the token's expiry. `COOKIE_SECURE` env var (default `true`) gates the `Secure` flag off for local non-HTTPS dev only.

**Rotation + reuse detection** (OWASP-recommended pattern): every successful `POST /api/auth/refresh` call:
1. Looks up the presented cookie's hash in `refresh_tokens`.
2. If the row doesn't exist or is expired → 401, generic "invalid session" message, cookie cleared.
3. If the row exists but `revoked_at` is already set → **reuse detected** (the token was already rotated away once before, meaning this presented copy is a stale replay — most likely theft). Response: 401, and **every** non-expired `RefreshToken` row for that `user_id` gets `revoked_at` set (full logout, all devices). This is a coarser mitigation than per-chain-only revocation (no `family_id` tracking) — accepted as adequate for this app's scale; note it as a place to revisit if session-family-level revocation is ever needed.
4. Otherwise: mark the current row `revoked_at=now`, insert a new `RefreshToken` row, set the new cookie, and issue a fresh access token JWT. Both old and new tokens' inserts/updates happen in one commit so a crash mid-rotation can't leave two live tokens.

New endpoints on `routers/auth.py` (still under `/api/auth`, matching this router's existing prefix — the `/strategies`-has-no-`/api` quirk documented elsewhere in this repo doesn't apply here):
- `POST /api/auth/refresh` — cookie in, new access token (JSON body) + rotated cookie out.
- `POST /api/auth/logout` — revokes the `RefreshToken` row matching the presented cookie (if any; no-op if already gone), clears the cookie. Only logs out the current device/session, not all of them.

`POST /api/auth/token` (login) is extended to also mint + set the refresh cookie on success, alongside its existing access-token response body. Its bad-credentials path changes from `return {"error": "Invalid credentials"}` (200) to `raise HTTPException(401, "Invalid credentials")`, matching the `WWW-Authenticate: Bearer` convention `get_current_user` already uses elsewhere. The message stays identical whether the email doesn't exist or the password is wrong, to avoid user enumeration.

## Login hardening

**Per-account lockout** — two new columns on `User`:
```python
failed_login_attempts = Column(Integer, default=0, nullable=False)
locked_until = Column(DateTime, nullable=True)
```
On a failed login: if `locked_until` is set and in the future, reject immediately with 429 (don't even check the password, don't increment further) and include the message but not the exact unlock time (avoid giving an attacker a precise countdown to resume on). Otherwise increment `failed_login_attempts`; on hitting `LOGIN_MAX_ATTEMPTS` (env var, default `5`), set `locked_until = now + LOGIN_LOCKOUT_MINUTES` (env var, default `15`) and reset the counter. On a successful login, reset `failed_login_attempts` to 0 and clear `locked_until`. This survives process restarts and works correctly across multiple backend replicas (state lives in Postgres, not in-process memory) — the reason this was chosen over a pure in-memory approach.

A login attempt against an email with no matching `User` row has no account to attach a counter to, so only the per-IP throttle below applies to it — this is unavoidable without inventing a shadow record for non-existent accounts, which isn't worth the complexity here. Note also that a 429 (locked) response necessarily reveals that the email belongs to a real, existing account, which a generic 401 does not — a small, accepted account-enumeration leak that's inherent to any lockout mechanism, not specific to this design.

**Per-IP throttle** on `/api/auth/token` and `/api/auth/refresh` specifically — a second, tighter `fixed_window` instance (reusing the existing class in `services/rate_limiter.py` unmodified), separate from `app.py`'s existing global 100 req/60s middleware. Applied as a small FastAPI dependency in `routers/auth.py` keyed by `request.client.host`, limit ~20 req/60s. This is a coarse first line of defense against credential-stuffing sprays across many accounts from one IP; it resets on restart, which is fine since the per-account lockout above is the durable defense.

## Frontend integration

- **`FrontEnd/src/api/client.js`**: add `withCredentials: true` to the axios instance so the refresh cookie is sent/received on same-site requests. Add a response interceptor: on a 401 from any request that isn't itself the login/refresh/logout call, attempt exactly one silent `POST /api/auth/refresh`; on success, retry the original request with the new access token; on failure, clear auth state and let the existing `ProtectedRoute` redirect handle the rest (no manual `window.location` redirect from inside the interceptor).
- **`FrontEnd/src/state/AuthContext.jsx`**: drop `localStorage` persistence of the access token entirely — it now lives only in React state (memory), never on disk, per the confirmed design decision. On mount, `AuthProvider` calls `/api/auth/refresh` once (relying on the cookie, if any) to silently rehydrate a session after a hard reload, gated behind a new `bootstrapping` boolean so children don't briefly render as "logged out" while that call is in flight. `login`/`register` store the returned access token in state (unchanged shape otherwise). `logout` calls the new `authApi.logout()` (revokes server-side + clears cookie) before clearing local state. A background `setTimeout`/`setInterval` refreshes proactively at ~80% of the access token's lifetime (~12 min) so a long-idle-but-open tab doesn't hit a 401 mid-action; cleared on unmount/logout.
- **`FrontEnd/src/components/ProtectedRoute.jsx`**: checks the new `bootstrapping` state and renders a loading state instead of redirecting to `/login` while the initial silent-refresh call is still pending.
- **`FrontEnd/src/api/auth.js`**: add `refresh()`/`logout()` wrappers alongside the existing `login`/`register`.
- CORS: `app.py`'s `CORSMiddleware` already sets `allow_credentials=True` with explicit (non-`*`) origins from `CORS_ORIGINS` — already compatible with cookie-based auth, no change needed.

## Deployment readiness

- `BackEnd/.env.example` gains: `ACCESS_TOKEN_EXPIRE_MINUTES=15`, `REFRESH_TOKEN_EXPIRE_DAYS=7`, `COOKIE_SECURE=true`, `LOGIN_MAX_ATTEMPTS=5`, `LOGIN_LOCKOUT_MINUTES=15`.
- `app.py` gets a fail-fast startup check: if `SECRET_KEY` is unset, empty, or equals the literal placeholder string from `.env.example` ("your-secret-key-here-change-this-in-production"), the app raises at import time instead of starting. Today `services/auth_service.py` reads it via bare `os.getenv` with no validation and only fails lazily on the first request that hits `get_current_user`/token creation — a misconfigured prod deploy would otherwise boot "successfully" and fail unpredictably per-request.
- New Alembic migration (`refresh_tokens` table + `User.failed_login_attempts`/`User.locked_until`), generated against a real Postgres instance and verified round-trip (`upgrade head` → `alembic check` → `downgrade`) per this repo's existing migration convention.

**Explicitly out of scope**, flagged for a separate pass if wanted: HTTPS-terminating reverse proxy configuration, a production (non-dev-volume-mounted) `docker-compose` variant, and a general security-headers middleware (HSTS/CSP/X-Frame-Options/etc.). Those are deployment concerns broader than login/auth itself.

## Testing

Following this repo's existing convention (real in-memory `sqlite+aiosqlite`, no mocks, `pytest-asyncio`):
- Rotation: refreshing issues a new access token and a new cookie value, and revokes the old `RefreshToken` row.
- Reuse detection: presenting an already-revoked refresh token 401s and revokes every other live token for that user.
- Lockout: `LOGIN_MAX_ATTEMPTS` failed logins locks the account, a locked account 429s even with the correct password, and the lockout clears after `LOGIN_LOCKOUT_MINUTES` (or on next success, whichever comes first).
- IP throttle: exceeding the login-route-specific limiter's threshold 429s independently of the account-level lockout.
- Cookie attributes: `httponly`/`secure`/`samesite`/`path` set as specified on both login and refresh responses, and cleared on logout.
- Bad-credentials response is 401 (not 200), with an identical message for "no such user" vs "wrong password".
- Existing ownership-check suite (`test_backtest_ownership.py`, `test_strategy_ownership.py`) stays green — `get_current_user`'s contract is unchanged, only the token's lifetime and the presence of the new refresh flow around it.
