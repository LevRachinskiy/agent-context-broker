from app.models.schemas import Event, EventCreate, EventKind, MemoryCreate
from app.providers.embeddings import EmbeddingProvider
from app.repositories.sql import Repository


class SessionService:
    def __init__(self, repository: Repository, embeddings: EmbeddingProvider):
        self.repository, self.embeddings = repository, embeddings

    async def append(self, session_id: str, data: EventCreate, key: str | None = None) -> Event:
        vector = await self.embeddings.embed(data.content)
        return await self.repository.append(session_id, data, vector, key)

    async def remember(self, session_id: str, data: MemoryCreate, key: str | None = None) -> Event:
        return await self.append(
            session_id, EventCreate(kind=EventKind.memory, **data.model_dump()), key
        )
