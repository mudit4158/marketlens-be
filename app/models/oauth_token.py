import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class OAuthToken(Base):
    """Stores OAuth access tokens for third-party providers (e.g. Upstox).
    One row per provider — upserted on each token refresh.
    """

    __tablename__ = "oauth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
