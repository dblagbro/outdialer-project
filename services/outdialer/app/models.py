from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[int] = mapped_column(Integer, default=0)
    caller_id_name: Mapped[str] = mapped_column(String(200), default="Devin's Out Caller")
    caller_id_number: Mapped[str] = mapped_column(String(80), default="")
    outbound_dial_prefix: Mapped[str] = mapped_column(String(40), default="")
    dial_normalization: Mapped[str] = mapped_column(String(40), default="nanp_1")
    call_window_start: Mapped[str] = mapped_column(String(5), default="10:00")
    call_window_end: Mapped[str] = mapped_column(String(5), default="19:30")
    timezone: Mapped[str] = mapped_column(String(80), default="America/New_York")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    retry_minutes: Mapped[int] = mapped_column(Integer, default=240)
    max_calls_per_worker_tick: Mapped[int] = mapped_column(Integer, default=1)
    ai_enabled: Mapped[int] = mapped_column(Integer, default=1)
    ai_provider: Mapped[str] = mapped_column(String(40), default="local")
    ai_observe_ms: Mapped[int] = mapped_column(Integer, default=0)
    ai_listen_ms: Mapped[int] = mapped_column(Integer, default=7000)
    ai_max_turns: Mapped[int] = mapped_column(Integer, default=3)
    ai_event_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_builder_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    flowise_api_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    flowise_chatflow_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    flowise_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    flowise_username: Mapped[str | None] = mapped_column(String(160), nullable=True)
    flowise_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    intro_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    voicemail_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice_prompt_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    attending_followup_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    thanks_attending_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    headcount_missing_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    thanks_not_attending_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    thanks_unsure_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    thanks_callback_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    no_response_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("campaign_id", "phone", name="uq_contacts_campaign_phone"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    campaign_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_digit: Mapped[str | None] = mapped_column(String(1), nullable=True)
    party_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party_kids: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party_friends: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party_family: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class CallAttempt(Base):
    __tablename__ = "call_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    campaign_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    contact_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    digit: Mapped[str | None] = mapped_column(String(1), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    amd_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    amd_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice_recording: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    dial_input: Mapped[str | None] = mapped_column(String(80), nullable=True)
    dial_normalization: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dialed_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    caller_id_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    caller_id_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sip_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    sip_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    sip_route: Mapped[str | None] = mapped_column(Text, nullable=True)
    sip_target: Mapped[str | None] = mapped_column(Text, nullable=True)
    sip_last_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    sip_last_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    party_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party_kids: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party_friends: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party_family: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventLog(Base):
    __tablename__ = "event_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    campaign_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    level: Mapped[str] = mapped_column(String(20), default="info")
    source: Mapped[str] = mapped_column(String(80), default="app")
    event_type: Mapped[str] = mapped_column(String(80), default="event")
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
