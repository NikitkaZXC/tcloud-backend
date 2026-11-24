import time
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import main
from src.config import settings
from src.infrastructure.security import jwt as tokens

ACCESS = settings.jwt.ACCESS_TOKEN_TYPE
REFRESH = settings.jwt.REFRESH_TOKEN_TYPE


@pytest.fixture
async def anonymous(client):
    other = AsyncClient(transport=ASGITransport(main.app), base_url="http://test/api")
    yield other
    await other.aclose()


def claims(subject: str, **values) -> dict:
    now = int(time.time())
    return {
        "sub": subject,
        "user_id": subject,
        "role": "root",
        "jti": str(uuid4()),
        "iat": now,
        "exp": now + 60,
        "token_type": ACCESS,
        **values,
    }


async def call(anonymous: AsyncClient, token: str):
    anonymous.cookies.set(ACCESS, token)
    response = await anonymous.get("/users/me")
    anonymous.cookies.clear()
    return response.status_code, response.json().get("error", {}).get("code")


async def test_access_token_error_branches(client, anonymous):
    me = (await client.get("/users/me")).json()["id"]
    key = tokens._private_key()
    expired = claims(me, exp=int(time.time()) - 5)
    no_exp = {k: v for k, v in claims(me).items() if k != "exp"}
    cases = {
        "expired": (jwt.encode(expired, key, "RS256"), "token_expired"),
        "no_exp": (jwt.encode(no_exp, key, "RS256"), "token_invalid"),
        "none": (jwt.encode(claims(me), None, "none"), "token_invalid"),
        "hs256": (jwt.encode(claims(me), "x" * 64, "HS256"), "token_invalid"),
        "refresh_type": (
            jwt.encode(claims(me, token_type=REFRESH), key, "RS256"),
            "token_invalid",
        ),
        "bad_user_id": (
            jwt.encode(claims(me, user_id="nope"), key, "RS256"),
            "token_invalid",
        ),
        "garbage": ("abc.def.ghi", "token_invalid"),
        "gone_user": (jwt.encode(claims(str(uuid4())), key, "RS256"), "token_invalid"),
    }
    for name, (token, expected) in cases.items():
        assert await call(anonymous, token) == (401, expected), name
    assert (await call(anonymous, jwt.encode(claims(me), key, "RS256")))[0] == 200


async def block_in_db(engine, user_id: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE users SET blocked = true WHERE id = :id"), {"id": user_id}
        )


async def test_blocked_user_is_refused_everywhere(client, make_user, anonymous, engine):
    user = await make_user("frozen")
    key = (
        await client.post(f"/users/{user.user_id}/api-keys", json={"name": "k"})
    ).json()["key"]
    await block_in_db(engine, user.user_id)
    by_key = await anonymous.get("/users/me", headers={"X-API-Key": key})
    assert by_key.status_code == 403
    assert by_key.json()["error"]["code"] == "user_blocked"
    by_cookie = await user.get("/users/me")
    assert by_cookie.status_code == 403
    assert by_cookie.json()["error"]["code"] == "user_blocked"
    refreshed = await user.post("/auth/refresh")
    assert refreshed.status_code == 403
    assert refreshed.json()["error"]["code"] == "user_blocked"
    assert (await user.get("/auth/sessions")).status_code == 401


async def test_unknown_api_key_and_refresh(anonymous):
    response = await anonymous.get("/users/me", headers={"X-API-Key": "tc_nope"})
    assert response.status_code == 401
    anonymous.cookies.set(REFRESH, "nope")
    assert (await anonymous.post("/auth/refresh")).status_code == 401
    anonymous.cookies.clear()
    assert (await anonymous.post("/auth/refresh")).status_code == 401
