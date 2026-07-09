import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AssetClass(str, enum.Enum):
    equity = "equity"
    commodity = "commodity"
    future = "future"
    index = "index"
    etf = "etf"
    currency = "currency"


class Instrument(Base):
    """A market instrument, identified internally regardless of which data source/ticker convention supplies its data."""

    __tablename__ = "instruments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_class: Mapped[AssetClass] = mapped_column(
        SAEnum(AssetClass, name="asset_class", native_enum=True, validate_strings=True), nullable=False
    )
    exchange_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchanges.id"), nullable=True
    )
    sector: Mapped[str | None] = mapped_column(String(150), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(150), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Provider-agnostic free-form extras (e.g. continuous-contract flag, lot size) that don't warrant a column yet.
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class InstrumentSourceMapping(Base):
    """Maps our internal Instrument to the ticker string a given DataSource expects, since providers disagree on ticker conventions."""

    __tablename__ = "instrument_source_mappings"
    __table_args__ = (UniqueConstraint("instrument_id", "source_id", name="uq_instrument_source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instruments.id"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False)
    source_ticker: Mapped[str] = mapped_column(String(50), nullable=False)
