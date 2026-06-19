from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = Field(alias="DATABASE_URL")
    caller_id_name: str = Field(default="Birthday RSVP", alias="CALLER_ID_NAME")
    caller_id_number: str = Field(default="15555551212", alias="CALLER_ID_NUMBER")
    outbound_dial_prefix: str = Field(default="", alias="OUTBOUND_DIAL_PREFIX")
    max_attempts: int = Field(default=3, alias="MAX_ATTEMPTS")
    retry_minutes: int = Field(default=240, alias="RETRY_MINUTES")
    call_window_start: str = Field(default="10:00", alias="CALL_WINDOW_START")
    call_window_end: str = Field(default="19:30", alias="CALL_WINDOW_END")
    timezone: str = Field(default="America/New_York", alias="TIMEZONE")
    max_calls_per_worker_tick: int = Field(default=1, alias="MAX_CALLS_PER_WORKER_TICK")
    worker_tick_seconds: int = Field(default=15, alias="WORKER_TICK_SECONDS")
    asterisk_outgoing_dir: str = Field(default="/asterisk-spool/outgoing", alias="ASTERISK_OUTGOING_DIR")
    asterisk_log_dir: str = Field(default="/asterisk-logs", alias="ASTERISK_LOG_DIR")


@lru_cache
def get_settings() -> Settings:
    return Settings()
