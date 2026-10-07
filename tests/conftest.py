import httpx
import pytest_asyncio

from app.api.application import create_app
from app.core.config import Settings


@pytest_asyncio.fixture
async def client(tmp_path):
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db", _env_file=None)
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            client.broker = app.state.container
            yield client


@pytest_asyncio.fixture
async def session_id(client):
    response = await client.post("/sessions", json={"agent_id": "test-agent", "token_budget": 128})
    assert response.status_code == 201
    return response.json()["session_id"]
