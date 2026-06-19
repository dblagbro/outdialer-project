from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from . import models  # noqa: F401

    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_lock(808801)"))
        try:
            Base.metadata.create_all(bind=connection)
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS campaigns (
                    id VARCHAR(36) PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    caller_id_name VARCHAR(200) NOT NULL DEFAULT 'Devin''s Out Caller',
                    caller_id_number VARCHAR(80) NOT NULL DEFAULT '',
                    outbound_dial_prefix VARCHAR(40) NOT NULL DEFAULT '',
                    dial_normalization VARCHAR(40) NOT NULL DEFAULT 'nanp_1',
                    call_window_start VARCHAR(5) NOT NULL DEFAULT '10:00',
                    call_window_end VARCHAR(5) NOT NULL DEFAULT '19:30',
                    timezone VARCHAR(80) NOT NULL DEFAULT 'America/New_York',
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    retry_minutes INTEGER NOT NULL DEFAULT 240,
                    max_calls_per_worker_tick INTEGER NOT NULL DEFAULT 1,
                    ai_enabled INTEGER NOT NULL DEFAULT 1,
                    ai_provider VARCHAR(40) NOT NULL DEFAULT 'local',
                    ai_observe_ms INTEGER NOT NULL DEFAULT 0,
                    ai_listen_ms INTEGER NOT NULL DEFAULT 7000,
                    ai_max_turns INTEGER NOT NULL DEFAULT 3,
                    ai_event_context TEXT,
                    ai_system_prompt TEXT,
                    ai_builder_notes TEXT,
                    flowise_api_url TEXT,
                    flowise_chatflow_id VARCHAR(160),
                    flowise_api_key TEXT,
                    flowise_username VARCHAR(160),
                    flowise_password TEXT,
                    intro_script TEXT,
                    voicemail_script TEXT,
                    voice_prompt_script TEXT,
                    thanks_attending_script TEXT,
                    thanks_not_attending_script TEXT,
                    thanks_unsure_script TEXT,
                    thanks_callback_script TEXT,
                    no_response_script TEXT,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """))
            for column in [
                "intro_script",
                "voicemail_script",
                "voice_prompt_script",
                "thanks_attending_script",
                "thanks_not_attending_script",
                "thanks_unsure_script",
                "thanks_callback_script",
                "no_response_script",
            ]:
                connection.execute(text(f"ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS {column} TEXT"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS dial_normalization VARCHAR(40) NOT NULL DEFAULT 'nanp_1'"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_enabled INTEGER NOT NULL DEFAULT 1"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_provider VARCHAR(40) NOT NULL DEFAULT 'local'"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_observe_ms INTEGER NOT NULL DEFAULT 0"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_listen_ms INTEGER NOT NULL DEFAULT 7000"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_max_turns INTEGER NOT NULL DEFAULT 3"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_event_context TEXT"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_system_prompt TEXT"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_builder_notes TEXT"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS flowise_api_url TEXT"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS flowise_chatflow_id VARCHAR(160)"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS flowise_api_key TEXT"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS flowise_username VARCHAR(160)"))
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS flowise_password TEXT"))
            connection.execute(text("UPDATE campaigns SET dial_normalization='nanp_1' WHERE dial_normalization IS NULL OR dial_normalization=''"))
            connection.execute(text("UPDATE campaigns SET ai_provider='local' WHERE ai_provider IS NULL OR ai_provider=''"))
            connection.execute(text("UPDATE campaigns SET ai_observe_ms=0 WHERE ai_observe_ms IS NULL OR ai_observe_ms < 0"))
            connection.execute(text("UPDATE campaigns SET ai_listen_ms=7000 WHERE ai_listen_ms IS NULL OR ai_listen_ms < 1000"))
            connection.execute(text("UPDATE campaigns SET ai_max_turns=3 WHERE ai_max_turns IS NULL OR ai_max_turns < 1"))
            connection.execute(text("""
                UPDATE campaigns SET
                    intro_script = coalesce(intro_script, 'Hi {contact_name}. This is Devin''s Out Caller. Press 1 if you are attending. Press 2 if you cannot attend. Press 3 if you are not sure. Press 9 if you would like a person to call you back. Or, after the tone, say yes, no, not sure, or call me back.'),
                    voicemail_script = coalesce(voicemail_script, 'Hello. This is Devin''s Out Caller. Please call us back. Goodbye.'),
                    voice_prompt_script = coalesce(voice_prompt_script, 'Please say yes, no, not sure, or call me back after the tone.'),
                    thanks_attending_script = coalesce(thanks_attending_script, 'Thank you. We have you marked as attending. Goodbye.'),
                    thanks_not_attending_script = coalesce(thanks_not_attending_script, 'Thank you. We have you marked as not attending. Goodbye.'),
                    thanks_unsure_script = coalesce(thanks_unsure_script, 'Thank you. We have you marked as unsure. Goodbye.'),
                    thanks_callback_script = coalesce(thanks_callback_script, 'Thank you. Someone will call you back. Goodbye.'),
                    no_response_script = coalesce(no_response_script, 'Sorry, we did not get a response. We may try again another time. Goodbye.')
            """))
            connection.execute(text("""
                INSERT INTO campaigns (
                    id, name, enabled, caller_id_name, caller_id_number, outbound_dial_prefix,
                    dial_normalization, call_window_start, call_window_end, timezone, max_attempts, retry_minutes,
                    max_calls_per_worker_tick, ai_enabled, ai_provider, ai_observe_ms, ai_listen_ms, ai_max_turns,
                    ai_event_context, ai_system_prompt, ai_builder_notes, flowise_api_url, flowise_chatflow_id,
                    flowise_api_key, flowise_username, flowise_password, intro_script, voicemail_script, voice_prompt_script,
                    thanks_attending_script, thanks_not_attending_script, thanks_unsure_script,
                    thanks_callback_script, no_response_script, created_at, updated_at
                )
                SELECT
                    'default', 'Birthday RSVP',
                    CASE WHEN coalesce((SELECT value FROM settings WHERE key='campaign_enabled'), 'false') = 'true' THEN 1 ELSE 0 END,
                    coalesce((SELECT value FROM settings WHERE key='caller_id_name'), 'Devin''s Out Caller'),
                    coalesce((SELECT value FROM settings WHERE key='caller_id_number'), ''),
                    coalesce((SELECT value FROM settings WHERE key='outbound_dial_prefix'), ''),
                    coalesce((SELECT value FROM settings WHERE key='dial_normalization'), 'nanp_1'),
                    coalesce((SELECT value FROM settings WHERE key='call_window_start'), '10:00'),
                    coalesce((SELECT value FROM settings WHERE key='call_window_end'), '19:30'),
                    coalesce((SELECT value FROM settings WHERE key='timezone'), 'America/New_York'),
                    coalesce(nullif((SELECT value FROM settings WHERE key='max_attempts'), '')::integer, 3),
                    coalesce(nullif((SELECT value FROM settings WHERE key='retry_minutes'), '')::integer, 240),
                    coalesce(nullif((SELECT value FROM settings WHERE key='max_calls_per_worker_tick'), '')::integer, 1),
                    1,
                    'local',
                    0,
                    7000,
                    3,
                    'Birthday RSVP call. Ask whether the contact is attending. Confirm yes, no, unsure, or callback requested. Keep responses warm, short, and family-friendly.',
                    'You are the call brain for Devin''s Out Caller. Return concise JSON actions only. Start speaking immediately when observe_ms is 0, distinguish voicemail from human speech when audio is available, and never mark RSVP unless the contact clearly answers.',
                    '',
                    coalesce((SELECT value FROM settings WHERE key='flowise_api_url'), 'http://gaid:3000/api/v1/prediction'),
                    coalesce((SELECT value FROM settings WHERE key='flowise_chatflow_id'), ''),
                    coalesce((SELECT value FROM settings WHERE key='flowise_api_key'), ''),
                    coalesce((SELECT value FROM settings WHERE key='flowise_username'), ''),
                    coalesce((SELECT value FROM settings WHERE key='flowise_password'), ''),
                    'Hi {contact_name}. This is Devin''s Out Caller. Press 1 if you are attending. Press 2 if you cannot attend. Press 3 if you are not sure. Press 9 if you would like a person to call you back. Or, after the tone, say yes, no, not sure, or call me back.',
                    'Hello. This is Devin''s Out Caller. Please call us back. Goodbye.',
                    'Please say yes, no, not sure, or call me back after the tone.',
                    'Thank you. We have you marked as attending. Goodbye.',
                    'Thank you. We have you marked as not attending. Goodbye.',
                    'Thank you. We have you marked as unsure. Goodbye.',
                    'Thank you. Someone will call you back. Goodbye.',
                    'Sorry, we did not get a response. We may try again another time. Goodbye.',
                    now(), now()
                WHERE NOT EXISTS (SELECT 1 FROM campaigns WHERE id='default')
            """))
            connection.execute(text("""
                UPDATE campaigns SET
                    intro_script = coalesce(intro_script, 'Hi {contact_name}. This is Devin''s Out Caller. Press 1 if you are attending. Press 2 if you cannot attend. Press 3 if you are not sure. Press 9 if you would like a person to call you back. Or, after the tone, say yes, no, not sure, or call me back.'),
                    voicemail_script = coalesce(voicemail_script, 'Hello. This is Devin''s Out Caller. Please call us back. Goodbye.'),
                    voice_prompt_script = coalesce(voice_prompt_script, 'Please say yes, no, not sure, or call me back after the tone.'),
                    thanks_attending_script = coalesce(thanks_attending_script, 'Thank you. We have you marked as attending. Goodbye.'),
                    thanks_not_attending_script = coalesce(thanks_not_attending_script, 'Thank you. We have you marked as not attending. Goodbye.'),
                    thanks_unsure_script = coalesce(thanks_unsure_script, 'Thank you. We have you marked as unsure. Goodbye.'),
                    thanks_callback_script = coalesce(thanks_callback_script, 'Thank you. Someone will call you back. Goodbye.'),
                    no_response_script = coalesce(no_response_script, 'Sorry, we did not get a response. We may try again another time. Goodbye.')
            """))
            connection.execute(text("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS campaign_id VARCHAR(36)"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS campaign_id VARCHAR(36)"))
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS event_logs (
                    id VARCHAR(36) PRIMARY KEY,
                    campaign_id VARCHAR(36),
                    level VARCHAR(20) NOT NULL DEFAULT 'info',
                    source VARCHAR(80) NOT NULL DEFAULT 'app',
                    event_type VARCHAR(80) NOT NULL DEFAULT 'event',
                    message TEXT NOT NULL,
                    details TEXT,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """))
            connection.execute(text("ALTER TABLE event_logs ADD COLUMN IF NOT EXISTS campaign_id VARCHAR(36)"))
            connection.execute(text("UPDATE contacts SET campaign_id='default' WHERE campaign_id IS NULL"))
            connection.execute(text("UPDATE call_attempts SET campaign_id='default' WHERE campaign_id IS NULL"))
            connection.execute(text("UPDATE event_logs SET campaign_id='default' WHERE campaign_id IS NULL"))
            connection.execute(text("ALTER TABLE contacts DROP CONSTRAINT IF EXISTS uq_contacts_phone"))
            connection.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'uq_contacts_campaign_phone'
                    ) THEN
                        ALTER TABLE contacts ADD CONSTRAINT uq_contacts_campaign_phone UNIQUE (campaign_id, phone);
                    END IF;
                END $$;
            """))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS amd_status VARCHAR(40)"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS amd_cause TEXT"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS voice_recording TEXT"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS transcript TEXT"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS dial_input VARCHAR(80)"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS dial_normalization VARCHAR(40)"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS dialed_number VARCHAR(80)"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS caller_id_name VARCHAR(200)"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS caller_id_number VARCHAR(80)"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS sip_to TEXT"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS sip_from TEXT"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS sip_route TEXT"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS sip_target TEXT"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS sip_last_response TEXT"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS sip_last_response_at TIMESTAMP WITH TIME ZONE"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS ai_decision TEXT"))
            connection.execute(text("ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS ai_trace TEXT"))
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS event_logs (
                    id VARCHAR(36) PRIMARY KEY,
                    level VARCHAR(20) NOT NULL DEFAULT 'info',
                    source VARCHAR(80) NOT NULL DEFAULT 'app',
                    event_type VARCHAR(80) NOT NULL DEFAULT 'event',
                    message TEXT NOT NULL,
                    details TEXT,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """))
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(808801)"))


def session_scope():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
