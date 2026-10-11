"""Small persisted records for each step of the Core pipeline."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    """Return an aware UTC timestamp."""
    return datetime.now(timezone.utc)


def new_id():
    """Make a compact unique identifier suitable for Telegram callback data."""
    return uuid4().hex


class Group(Base):
    __tablename__ = "groups"
    chat_id = Column(BigInteger, primary_key=True, autoincrement=False)
    status = Column(String, default="paused", nullable=False)
    consent_at = Column(DateTime(timezone=True))
    delete_token = Column(String)
    delete_expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class AnalysisWindow(Base):
    __tablename__ = "analysis_windows"
    id = Column(String, primary_key=True, default=new_id)
    chat_id = Column(BigInteger, ForeignKey("groups.chat_id", ondelete="CASCADE"), index=True, nullable=False)
    status = Column(String, default="analyzing", nullable=False)
    lore = Column(JSON)
    error = Column(String)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("chat_id", "telegram_message_id"),)
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, ForeignKey("groups.chat_id", ondelete="CASCADE"), index=True, nullable=False)
    telegram_message_id = Column(BigInteger, nullable=False)
    sender_id = Column(BigInteger, nullable=False)
    sender_name = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    reply_to_message_id = Column(BigInteger)
    window_id = Column(String, ForeignKey("analysis_windows.id", ondelete="CASCADE"), index=True)


class WindowMessage(Base):
    __tablename__ = "window_messages"
    window_id = Column(String, ForeignKey("analysis_windows.id", ondelete="CASCADE"), primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)


class LoreProposal(Base):
    __tablename__ = "lore_proposals"
    id = Column(String, primary_key=True, default=new_id)
    chat_id = Column(BigInteger, ForeignKey("groups.chat_id", ondelete="CASCADE"), index=True, nullable=False)
    window_id = Column(String, ForeignKey("analysis_windows.id", ondelete="CASCADE"), unique=True, nullable=False)
    lore = Column(JSON, nullable=False)
    categories = Column(JSON, nullable=False)
    status = Column(String, default="voting", nullable=False)
    round = Column(Integer, default=1, nullable=False)
    closes_at = Column(DateTime(timezone=True))
    telegram_message_id = Column(BigInteger)
    winner = Column(JSON)
    generation_request = Column(JSON)
    job_id = Column(String)
    terminal_job_failure = Column(Boolean, default=False, nullable=False)
    error = Column(String)
    notified = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("proposal_id", "round", "user_id"),)
    id = Column(Integer, primary_key=True)
    proposal_id = Column(String, ForeignKey("lore_proposals.id", ondelete="CASCADE"), nullable=False, index=True)
    round = Column(Integer, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    choice = Column(Integer, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)


class GeneratedProduct(Base):
    __tablename__ = "generated_products"
    id = Column(Integer, primary_key=True)
    proposal_id = Column(String, ForeignKey("lore_proposals.id", ondelete="CASCADE"), unique=True, nullable=False)
    arcade_product_id = Column(String, nullable=False)
    category = Column(JSON, nullable=False)
    price = Column(JSON, nullable=False)
    image_url = Column(Text)
    details = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
