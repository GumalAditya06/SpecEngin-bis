from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncEngine,
    AsyncSession,
)

from app.core.config import settings


def _engine_url(value: str) -> str:
    """Accept Render's standard Postgres URL with this async SQLAlchemy app."""
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    return value


engine: AsyncEngine = create_async_engine(
    _engine_url(settings.database_url),
    echo=False,
    future=True,
)

async_session_local = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with async_session_local() as session:
        yield session
