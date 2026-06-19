import base64
import csv
import json
import os
import re
from collections import deque
from datetime import datetime, timezone
from html import escape
from io import StringIO
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import session_scope
from .models import CallAttempt, Campaign, Contact, EventLog, now_utc

app = FastAPI(title="Devin's Out Caller")

CSV_TEMPLATE = "name,phone,notes\nJane Example,+18455551212,Needs callback after 5 PM\nJohn Example,5555,Internal test extension\n"
DEFAULT_CAMPAIGN_ID = "default"
RECORDINGS_DIR = "/recordings"
ASTERISK_LOG_DIR = os.getenv("ASTERISK_LOG_DIR", "/asterisk-logs")
STATUSES = [
    "pending",
    "attending",
    "not_attending",
    "unsure",
    "callback_requested",
    "voice_response",
    "voicemail_left",
    "no_response",
]
TRACE_LIMITS = ["10", "50", "100", "250", "500", "all"]
TABLE_LIMITS = ["25", "50", "100", "250", "500"]
CONTACT_REFRESH_OPTIONS = {
    "0": "Off",
    "5": "5 sec",
    "10": "10 sec",
    "15": "15 sec",
    "30": "30 sec",
    "60": "60 sec",
}
DIAL_NORMALIZATION_OPTIONS = {
    "nanp_1": "NANP: add 1 to 10-digit numbers",
    "strip_only": "Strip punctuation only",
    "as_entered": "Keep +, *, and # as entered",
}
SCRIPT_FIELDS = {
    "intro_script": "Hi {contact_name}. This is Devin's Out Caller. Press 1 if you are attending. Press 2 if you cannot attend. Press 3 if you are not sure. Press 9 if you would like a person to call you back. Or, after the tone, say yes, no, not sure, or call me back.",
    "voicemail_script": "Hello. This is Devin's Out Caller. Please call us back. Goodbye.",
    "voice_prompt_script": "Please say yes, no, not sure, or call me back after the tone.",
    "thanks_attending_script": "Thank you. We have you marked as attending. Goodbye.",
    "thanks_not_attending_script": "Thank you. We have you marked as not attending. Goodbye.",
    "thanks_unsure_script": "Thank you. We have you marked as unsure. Goodbye.",
    "thanks_callback_script": "Thank you. Someone will call you back. Goodbye.",
    "no_response_script": "Sorry, we did not get a response. We may try again another time. Goodbye.",
}
AI_PROVIDERS = {
    "local": "Local rule fallback",
    "flowise": "Flowise chatflow",
}
DEFAULT_AI_EVENT_CONTEXT = (
    "Birthday RSVP call. Ask whether the contact is attending. Confirm yes, no, unsure, "
    "or callback requested. Keep responses warm, short, and family-friendly."
)
DEFAULT_AI_SYSTEM_PROMPT = (
    "You are the call brain for Devin's Out Caller. Return concise JSON actions only. "
    "Start speaking immediately when observe_ms is 0, distinguish voicemail from human speech when audio is available, "
    "and never mark RSVP unless the contact clearly answers."
)
AI_DECISION_KEYS = {
    "action",
    "text",
    "digit",
    "rsvp",
    "status",
    "reason",
    "confidence",
    "listen_ms",
    "hangup_after",
    "source",
}
SIP_LINE_RE = re.compile(
    r"(<--- (Transmitting|Received)|SIP/2\.0 \d{3}|^(Via|From|To|Call-ID|CSeq|Contact|Route|Record-Route|P-Asserted-Identity|Remote-Party-ID):|"
    r"\b(INVITE|ACK|BYE|CANCEL|OPTIONS|REGISTER) sip:)",
    re.I,
)


def get_db():
    yield from session_scope()


def wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def with_params(path: str, **params: str) -> str:
    return f"{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"


def see_other(request: Request, message: str = "", **params: str) -> RedirectResponse:
    target = request.headers.get("referer") or "./"
    parts = urlsplit(target)
    query = dict([part.split("=", 1) for part in parts.query.split("&") if "=" in part])
    query.update(params)
    if message:
        query["message"] = message
    target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return RedirectResponse(target, status_code=303)


def add_event(db: Session, event_type: str, message: str, campaign_id: str | None = None, source: str = "api", level: str = "info", details: str | None = None) -> None:
    db.add(EventLog(campaign_id=campaign_id, level=level, source=source, event_type=event_type, message=message, details=details))
    db.commit()


def apply_script_defaults(campaign: Campaign) -> None:
    for field, value in SCRIPT_FIELDS.items():
        if not getattr(campaign, field, None):
            setattr(campaign, field, value)
    if getattr(campaign, "dial_normalization", None) not in DIAL_NORMALIZATION_OPTIONS:
        campaign.dial_normalization = "nanp_1"
    if getattr(campaign, "ai_provider", None) not in AI_PROVIDERS:
        campaign.ai_provider = "local"
    if getattr(campaign, "ai_observe_ms", None) is None or campaign.ai_observe_ms < 0:
        campaign.ai_observe_ms = 0
    if not getattr(campaign, "ai_listen_ms", None) or campaign.ai_listen_ms < 1000:
        campaign.ai_listen_ms = 7000
    if not getattr(campaign, "ai_max_turns", None) or campaign.ai_max_turns < 1:
        campaign.ai_max_turns = 3
    if not getattr(campaign, "ai_event_context", None):
        campaign.ai_event_context = DEFAULT_AI_EVENT_CONTEXT
    if not getattr(campaign, "ai_system_prompt", None):
        campaign.ai_system_prompt = DEFAULT_AI_SYSTEM_PROMPT
    if getattr(campaign, "flowise_api_url", None) is None:
        campaign.flowise_api_url = "http://gaid:3000/api/v1/prediction"


def clean_dial_normalization(value: str) -> str:
    return value if value in DIAL_NORMALIZATION_OPTIONS else "nanp_1"


def dial_normalization_label(value: str | None) -> str:
    return DIAL_NORMALIZATION_OPTIONS.get(value or "", DIAL_NORMALIZATION_OPTIONS["nanp_1"])


def script_value(campaign: Campaign, field: str) -> str:
    return getattr(campaign, field, None) or SCRIPT_FIELDS[field]


def clean_ai_provider(value: str) -> str:
    return value if value in AI_PROVIDERS else "local"


def truthy_form(value: str | None) -> int:
    return 1 if (value or "").lower() in {"1", "true", "yes", "on"} else 0


def clamp_int(value: str | int | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def flowise_prediction_url(campaign: Campaign) -> str:
    base_url = (campaign.flowise_api_url or os.getenv("FLOWISE_API_URL") or "http://gaid:3000/api/v1/prediction").strip().rstrip("/")
    chatflow_id = (campaign.flowise_chatflow_id or os.getenv("FLOWISE_CHATFLOW_ID") or "").strip()
    if "{chatflow_id}" in base_url:
        return base_url.replace("{chatflow_id}", quote(chatflow_id))
    if chatflow_id and base_url.endswith("/prediction"):
        return f"{base_url}/{quote(chatflow_id)}"
    return base_url


def compact_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"))


def extract_json_object(text: str) -> dict[str, object] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def render_for_contact(campaign: Campaign, contact: Contact | None, template: str) -> str:
    values = {
        "campaign_id": campaign.id,
        "contact_name": contact.name if contact else "there",
        "callback_number": os.getenv("CALLBACK_NUMBER", ""),
    }
    class SafeVars(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    return (template or "").format_map(SafeVars(values))


def digit_status(digit: str | None) -> str:
    return {"1": "attending", "2": "not_attending", "3": "unsure", "9": "callback_requested"}.get(digit or "", "no_response")


def classify_ai_rsvp(transcript: str, digit: str = "") -> str:
    if digit in {"1", "2", "3", "9"}:
        return digit
    text = (transcript or "").lower()
    if re.search(r"\b(call me|callback|call back|person call|talk to)\b", text):
        return "9"
    if re.search(r"\b(maybe|not sure|unsure|don't know|do not know)\b", text):
        return "3"
    if re.search(r"\b(can't|cannot|won't|unable|not coming|not attending|no)\b", text):
        return "2"
    if re.search(r"\b(yes|attending|coming|will be there|we'll be there|i'll be there)\b", text):
        return "1"
    return ""


def looks_like_voicemail(transcript: str) -> bool:
    text = (transcript or "").lower()
    if not text:
        return False
    patterns = [
        r"\bvoice\s*mail\b",
        r"\bmailbox\b",
        r"\bautomated voice messaging system\b",
        r"\bthe person you (are trying to reach|called)\b",
        r"\bthe subscriber you (have )?called\b",
        r"\bis not available\b",
        r"\bnot available to take your call\b",
        r"\bplease leave (a )?message\b",
        r"\bleave your message\b",
        r"\bafter the tone\b",
        r"\brecord your message\b",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def thanks_for_digit(campaign: Campaign, contact: Contact | None, digit: str) -> str:
    field = {
        "1": "thanks_attending_script",
        "2": "thanks_not_attending_script",
        "3": "thanks_unsure_script",
        "9": "thanks_callback_script",
    }.get(digit, "no_response_script")
    return render_for_contact(campaign, contact, script_value(campaign, field))


def normalize_ai_decision(raw: dict[str, object], fallback_text: str = "") -> dict[str, object]:
    decision = {key: raw.get(key) for key in AI_DECISION_KEYS if key in raw}
    action = str(decision.get("action") or "").strip().lower()
    if action not in {"legacy", "leave_voicemail", "speak_and_listen", "mark_rsvp", "complete", "hangup"}:
        action = "speak_and_listen" if (decision.get("text") or fallback_text) else "complete"
    decision["action"] = action
    if fallback_text and not decision.get("text"):
        decision["text"] = fallback_text
    if decision.get("digit") not in {"1", "2", "3", "9", "", None}:
        decision["digit"] = ""
    if "listen_ms" in decision:
        decision["listen_ms"] = clamp_int(decision.get("listen_ms"), 7000, 1000, 20000)
    decision["hangup_after"] = bool(decision.get("hangup_after")) if "hangup_after" in decision else action in {"leave_voicemail", "mark_rsvp", "complete", "hangup"}
    return decision


def local_ai_decision(campaign: Campaign, contact: Contact | None, payload: dict[str, object], reason: str = "") -> dict[str, object]:
    stage = str(payload.get("stage") or "")
    transcript = str(payload.get("transcript") or "")
    answer_class = str(payload.get("answer_class") or "")
    digit = str(payload.get("digit") or "")
    turn = clamp_int(payload.get("turn"), 0, 0, campaign.ai_max_turns or 3)
    classified_digit = classify_ai_rsvp(transcript, digit)
    source = "local_fallback" if reason else "local"

    if stage in {"answer_observed", "human_response"} and (answer_class == "machine" or looks_like_voicemail(transcript)):
        return normalize_ai_decision(
            {
                "action": "leave_voicemail",
                "text": render_for_contact(campaign, contact, script_value(campaign, "voicemail_script")),
                "status": "voicemail_left",
                "reason": reason or ("answer looked like voicemail" if answer_class == "machine" else "transcript looked like voicemail"),
                "source": source,
            }
        )
    if stage in {"answer_observed", "dtmf_response", "human_response"} and classified_digit:
        return normalize_ai_decision(
            {
                "action": "mark_rsvp",
                "digit": classified_digit,
                "rsvp": digit_status(classified_digit),
                "status": digit_status(classified_digit),
                "text": thanks_for_digit(campaign, contact, classified_digit),
                "reason": reason or "classified contact response",
                "source": source,
            }
        )
    if stage == "human_response" and turn >= (campaign.ai_max_turns or 3):
        return normalize_ai_decision(
            {
                "action": "complete",
                "status": "no_response",
                "text": render_for_contact(campaign, contact, script_value(campaign, "no_response_script")),
                "reason": reason or "max AI turns reached without a clear RSVP",
                "source": source,
            }
        )
    text = (
        render_for_contact(campaign, contact, script_value(campaign, "voice_prompt_script"))
        if stage == "human_response"
        else render_for_contact(campaign, contact, script_value(campaign, "intro_script"))
    )
    return normalize_ai_decision(
        {
            "action": "speak_and_listen",
            "text": text,
            "status": "in_conversation",
            "listen_ms": campaign.ai_listen_ms or 7000,
            "reason": reason or "continue conversation",
            "source": source,
        }
    )


def call_flowise(campaign: Campaign, contact: Contact | None, payload: dict[str, object]) -> tuple[dict[str, object] | None, str]:
    if clean_ai_provider(campaign.ai_provider) != "flowise" or not campaign.flowise_chatflow_id:
        return None, "Flowise provider is not configured"
    question = {
        "instruction": "Return one JSON object with action, text, digit, status, reason, listen_ms, and hangup_after. Do not wrap it in markdown.",
        "campaign": campaign.name,
        "event_context": campaign.ai_event_context or DEFAULT_AI_EVENT_CONTEXT,
        "system_prompt": campaign.ai_system_prompt or DEFAULT_AI_SYSTEM_PROMPT,
        "contact": {"name": contact.name if contact else "", "phone": contact.phone if contact else ""},
        "call_state": payload,
        "valid_actions": ["leave_voicemail", "speak_and_listen", "mark_rsvp", "complete", "hangup"],
        "valid_digits": {"1": "attending", "2": "not_attending", "3": "unsure", "9": "callback_requested"},
    }
    headers = {"Content-Type": "application/json"}
    api_key = (campaign.flowise_api_key or os.getenv("FLOWISE_API_KEY") or "").strip()
    username = (campaign.flowise_username or os.getenv("FLOWISE_USERNAME") or "").strip()
    password = (campaign.flowise_password or os.getenv("FLOWISE_PASSWORD") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif username or password:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    body = json.dumps({"question": json.dumps(question, ensure_ascii=True)}).encode("utf-8")
    request = urllib.request.Request(flowise_prediction_url(campaign), data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return None, f"Flowise HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}"
    except Exception as exc:
        return None, f"Flowise request failed: {exc}"

    parsed = extract_json_object(raw)
    if not parsed:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None, f"Flowise returned non-JSON: {raw[:300]}"
        if isinstance(data, dict):
            for key in ["text", "answer", "result", "output"]:
                parsed = extract_json_object(str(data.get(key) or ""))
                if parsed:
                    break
            if not parsed and "action" in data:
                parsed = data
    if not parsed:
        return None, f"Flowise response did not include an action JSON object: {raw[:300]}"
    decision = normalize_ai_decision(parsed)
    decision["source"] = "flowise"
    return decision, ""


def append_ai_trace(db: Session, attempt: CallAttempt | None, payload: dict[str, object], decision: dict[str, object]) -> None:
    if not attempt:
        return
    try:
        trace = json.loads(attempt.ai_trace or "[]")
        if not isinstance(trace, list):
            trace = []
    except json.JSONDecodeError:
        trace = []
    trace.append(
        {
            "at": now_utc().isoformat(),
            "stage": payload.get("stage"),
            "turn": payload.get("turn"),
            "answer_class": payload.get("answer_class"),
            "digit": payload.get("digit"),
            "transcript": str(payload.get("transcript") or "")[:500],
            "decision": decision,
        }
    )
    attempt.ai_decision = compact_json(decision)
    attempt.ai_trace = json.dumps(trace[-20:], ensure_ascii=True)
    db.commit()


def ensure_campaign(db: Session, campaign_id: str | None = None) -> Campaign:
    campaign = db.get(Campaign, campaign_id or DEFAULT_CAMPAIGN_ID)
    if campaign:
        apply_script_defaults(campaign)
        return campaign
    campaign = Campaign(id=DEFAULT_CAMPAIGN_ID, name="Birthday RSVP")
    apply_script_defaults(campaign)
    db.add(campaign)
    db.commit()
    return campaign


def campaigns(db: Session) -> list[Campaign]:
    items = db.scalars(select(Campaign).order_by(Campaign.created_at)).all()
    if not items:
        items = [ensure_campaign(db)]
    return items


def format_dt(value: datetime | None) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def parse_log_dt(line: str) -> datetime | None:
    if not line.startswith("[") or len(line) < 21:
        return None
    try:
        return datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def after_clear_marker(db: Session, line: str) -> bool:
    marker = db.get(Campaign, DEFAULT_CAMPAIGN_ID)
    _ = marker
    return True


def sip_trace_lines(db: Session, limit: str = "100", order: str = "newest", text_filter: str = "") -> list[str]:
    path = Path(ASTERISK_LOG_DIR) / "messages"
    if not path.exists():
        return []
    clear_setting = db.get(Campaign, DEFAULT_CAMPAIGN_ID)
    _ = clear_setting
    cleared_at_value = None
    # Reuse a synthetic event as the clear marker so no schema change is needed.
    clear_event = db.scalar(select(EventLog).where(EventLog.event_type == "sip_trace_cleared").order_by(EventLog.created_at.desc()).limit(1))
    if clear_event:
        cleared_at_value = clear_event.created_at

    needle = text_filter.lower().strip()
    maxlen = None if limit == "all" else max(int(limit), 1)
    lines = [] if maxlen is None else deque(maxlen=maxlen)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip()
            if not SIP_LINE_RE.search(line):
                continue
            line_dt = parse_log_dt(line)
            if cleared_at_value and line_dt and line_dt <= cleared_at_value:
                continue
            if needle and needle not in line.lower():
                continue
            lines.append(line)
    result = list(lines)
    if order == "newest":
        result.reverse()
    return result


def contact_dict(contact: Contact) -> dict[str, object]:
    return {
        "id": contact.id,
        "campaign_id": contact.campaign_id,
        "name": contact.name,
        "phone": contact.phone,
        "status": contact.status,
        "attempts": contact.attempts,
        "last_digit": contact.last_digit,
        "next_call_at": contact.next_call_at,
        "notes": contact.notes,
    }


def log_dict(attempt: CallAttempt, contact: Contact | None) -> dict[str, object]:
    return {
        "id": attempt.id,
        "campaign_id": attempt.campaign_id,
        "contact_id": attempt.contact_id,
        "name": contact.name if contact else "",
        "phone": contact.phone if contact else "",
        "status": attempt.status,
        "digit": attempt.digit,
        "message": attempt.message,
        "amd_status": attempt.amd_status,
        "amd_cause": attempt.amd_cause,
        "voice_recording": attempt.voice_recording,
        "transcript": attempt.transcript,
        "dial_input": attempt.dial_input,
        "dial_normalization": attempt.dial_normalization,
        "dialed_number": attempt.dialed_number,
        "caller_id_name": attempt.caller_id_name,
        "caller_id_number": attempt.caller_id_number,
        "sip_to": attempt.sip_to,
        "sip_from": attempt.sip_from,
        "sip_route": attempt.sip_route,
        "sip_target": attempt.sip_target,
        "sip_last_response": attempt.sip_last_response,
        "sip_last_response_at": attempt.sip_last_response_at,
        "ai_decision": attempt.ai_decision,
        "ai_trace": attempt.ai_trace,
        "created_at": attempt.created_at,
        "completed_at": attempt.completed_at,
    }


def recent_logs(
    db: Session,
    campaign_id: str,
    limit: int = 50,
    order: str = "newest",
    status_filter: str = "",
    text_filter: str = "",
) -> list[dict[str, object]]:
    query = select(CallAttempt).where(CallAttempt.campaign_id == campaign_id)
    if status_filter:
        query = query.where(CallAttempt.status == status_filter)
    if text_filter:
        pattern = f"%{text_filter}%"
        query = query.where(
            CallAttempt.message.ilike(pattern)
            | CallAttempt.dial_input.ilike(pattern)
            | CallAttempt.dial_normalization.ilike(pattern)
            | CallAttempt.dialed_number.ilike(pattern)
            | CallAttempt.caller_id_number.ilike(pattern)
            | CallAttempt.sip_to.ilike(pattern)
            | CallAttempt.sip_from.ilike(pattern)
            | CallAttempt.sip_route.ilike(pattern)
            | CallAttempt.sip_last_response.ilike(pattern)
            | CallAttempt.transcript.ilike(pattern)
            | CallAttempt.ai_decision.ilike(pattern)
            | CallAttempt.ai_trace.ilike(pattern)
        )
    sort_column = CallAttempt.created_at.asc() if order == "oldest" else CallAttempt.created_at.desc()
    attempts = db.scalars(query.order_by(sort_column).limit(limit)).all()
    contact_ids = [attempt.contact_id for attempt in attempts]
    contacts = {}
    if contact_ids:
        contacts = {contact.id: contact for contact in db.scalars(select(Contact).where(Contact.id.in_(contact_ids))).all()}
    return [log_dict(attempt, contacts.get(attempt.contact_id)) for attempt in attempts]


def recent_events(
    db: Session,
    campaign_id: str,
    limit: int = 80,
    order: str = "newest",
    text_filter: str = "",
) -> list[EventLog]:
    query = select(EventLog).where((EventLog.campaign_id == campaign_id) | (EventLog.campaign_id.is_(None)))
    if text_filter:
        pattern = f"%{text_filter}%"
        query = query.where(
            EventLog.level.ilike(pattern)
            | EventLog.source.ilike(pattern)
            | EventLog.event_type.ilike(pattern)
            | EventLog.message.ilike(pattern)
            | EventLog.details.ilike(pattern)
        )
    sort_column = EventLog.created_at.asc() if order == "oldest" else EventLog.created_at.desc()
    return db.scalars(query.order_by(sort_column).limit(limit)).all()


def get_status(db: Session, campaign_id: str) -> dict[str, object]:
    campaign = ensure_campaign(db, campaign_id)
    now = now_utc()
    contact_counts = dict(db.execute(select(Contact.status, func.count()).where(Contact.campaign_id == campaign_id).group_by(Contact.status)).all())
    attempt_counts = dict(db.execute(select(CallAttempt.status, func.count()).where(CallAttempt.campaign_id == campaign_id).group_by(CallAttempt.status)).all())
    total_contacts = db.scalar(select(func.count()).select_from(Contact).where(Contact.campaign_id == campaign_id)) or 0
    total_attempts = db.scalar(select(func.count()).select_from(CallAttempt).where(CallAttempt.campaign_id == campaign_id)) or 0
    last_attempt = db.scalar(select(func.max(CallAttempt.created_at)).where(CallAttempt.campaign_id == campaign_id))
    retryable_statuses = ["pending", "no_response"]
    eligible_now = db.scalar(
        select(func.count())
        .select_from(Contact)
        .where(Contact.campaign_id == campaign_id)
        .where(Contact.status.in_(retryable_statuses))
        .where(Contact.attempts < campaign.max_attempts)
        .where(or_(Contact.next_call_at.is_(None), Contact.next_call_at <= now))
    ) or 0
    waiting_retry = db.scalar(
        select(func.count())
        .select_from(Contact)
        .where(Contact.campaign_id == campaign_id)
        .where(Contact.status.in_(retryable_statuses))
        .where(Contact.attempts < campaign.max_attempts)
        .where(Contact.next_call_at.is_not(None))
        .where(Contact.next_call_at > now)
    ) or 0
    next_retry_at = db.scalar(
        select(func.min(Contact.next_call_at))
        .where(Contact.campaign_id == campaign_id)
        .where(Contact.status.in_(retryable_statuses))
        .where(Contact.attempts < campaign.max_attempts)
        .where(Contact.next_call_at.is_not(None))
        .where(Contact.next_call_at > now)
    )
    maxed_out = db.scalar(
        select(func.count())
        .select_from(Contact)
        .where(Contact.campaign_id == campaign_id)
        .where(Contact.status.in_(retryable_statuses))
        .where(Contact.attempts >= campaign.max_attempts)
    ) or 0
    return {
        "campaign_enabled": bool(campaign.enabled),
        "total_contacts": total_contacts,
        "total_attempts": total_attempts,
        "last_attempt_at": last_attempt,
        "eligible_now": int(eligible_now),
        "waiting_retry": int(waiting_retry),
        "next_retry_at": next_retry_at,
        "maxed_out": int(maxed_out),
        "contacts": {status: int(contact_counts.get(status, 0)) for status in STATUSES},
        "attempts": {key: int(value) for key, value in attempt_counts.items()},
    }


def import_csv(content: str, db: Session, campaign_id: str) -> dict[str, int]:
    reader = csv.DictReader(StringIO(content))
    if not reader.fieldnames or "name" not in reader.fieldnames or "phone" not in reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV must include name and phone columns")
    imported = skipped = updated = 0
    for row in reader:
        name = (row.get("name") or "").strip()
        phone = (row.get("phone") or "").strip()
        notes = (row.get("notes") or "").strip() or None
        if not name or not phone:
            skipped += 1
            continue
        existing = db.scalar(select(Contact).where(Contact.campaign_id == campaign_id).where(Contact.phone == phone))
        if existing:
            existing.name = name
            existing.notes = notes
            existing.updated_at = now_utc()
            db.commit()
            updated += 1
            continue
        db.add(Contact(campaign_id=campaign_id, name=name, phone=phone, notes=notes))
        try:
            db.commit()
            imported += 1
        except IntegrityError:
            db.rollback()
            skipped += 1
    return {"imported": imported, "updated": updated, "skipped": skipped}


def recording_link(filename: object) -> str:
    if not filename:
        return ""
    safe = str(filename).replace("/", "").replace("\\", "")
    return f'<a href="recordings/{escape(safe)}">Download</a>'


def tab_link(label: str, tab: str, active: str, campaign_id: str, **params: str) -> str:
    css = "tab active" if tab == active else "tab"
    return f'<a class="{css}" href="{with_params("./", campaign_id=campaign_id, tab=tab, **params)}">{escape(label)}</a>'


def help_button(text: str) -> str:
    return f'<button class="help" type="button" title="{escape(text)}">?</button>'


def field_label(text: str, help_text: str) -> str:
    return f'<span>{escape(text)} {help_button(help_text)}</span>'


def render_admin(
    db: Session,
    campaign_id: str | None = None,
    tab: str = "dashboard",
    message: str = "",
    log_order: str = "newest",
    log_limit: str = "100",
    log_filter: str = "",
    log_status: str = "",
    event_order: str = "newest",
    event_limit: str = "100",
    event_filter: str = "",
    trace_order: str = "newest",
    trace_limit: str = "100",
    trace_filter: str = "",
    contact_refresh: str = "10",
) -> str:
    campaign = ensure_campaign(db, campaign_id)
    all_campaigns = campaigns(db)
    status = get_status(db, campaign.id)
    contacts = db.scalars(select(Contact).where(Contact.campaign_id == campaign.id).order_by(Contact.created_at)).all()
    if log_limit not in TABLE_LIMITS:
        log_limit = "100"
    if event_limit not in TABLE_LIMITS:
        event_limit = "100"
    if log_order not in {"newest", "oldest"}:
        log_order = "newest"
    if event_order not in {"newest", "oldest"}:
        event_order = "newest"
    if log_status not in {"", *STATUSES, "queued", "originated", "failed"}:
        log_status = ""
    logs = recent_logs(db, campaign.id, int(log_limit), log_order, log_status, log_filter)
    events = recent_events(db, campaign.id, int(event_limit), event_order, event_filter)
    if trace_limit not in TRACE_LIMITS:
        trace_limit = "100"
    if trace_order not in {"newest", "oldest"}:
        trace_order = "newest"
    if contact_refresh not in CONTACT_REFRESH_OPTIONS:
        contact_refresh = "10"
    sip_lines = sip_trace_lines(db, trace_limit, trace_order, trace_filter)

    campaign_options = "".join(
        f'<option value="{escape(item.id)}"{" selected" if item.id == campaign.id else ""}>{escape(item.name)}</option>'
        for item in all_campaigns
    )
    status_cards = [
        ("Campaign", "Running" if campaign.enabled else "Stopped", "good" if campaign.enabled else "muted"),
        ("Eligible Now", str(status["eligible_now"]), "good" if status["eligible_now"] else "muted"),
        ("Waiting Retry", str(status["waiting_retry"]), "warn" if status["waiting_retry"] else "muted"),
        ("Contacts", str(status["total_contacts"]), ""),
        ("Attempts", str(status["total_attempts"]), ""),
        ("AI Brain", "On" if campaign.ai_enabled else "Off", "good" if campaign.ai_enabled else "muted"),
        ("AI Provider", AI_PROVIDERS.get(clean_ai_provider(campaign.ai_provider), "Local"), ""),
        ("Attending", str(status["contacts"]["attending"]), "good"),
        ("Not Attending", str(status["contacts"]["not_attending"]), "bad"),
        ("Needs Followup", str(status["contacts"]["unsure"] + status["contacts"]["callback_requested"] + status["contacts"]["voice_response"] + status["contacts"]["no_response"]), "warn"),
    ]
    cards_html = "\n".join(f'<div class="stat {css}"><span>{escape(label)}</span><strong>{escape(value)}</strong></div>' for label, value, css in status_cards)

    contact_rows = []
    for contact in contacts:
        options = "".join(
            f'<option value="{escape(option)}"{" selected" if contact.status == option else ""}>{escape(option.replace("_", " ").title())}</option>'
            for option in STATUSES
        )
        contact_rows.append(f"""
            <tr>
              <td><form id="edit-{escape(contact.id)}" action="contacts/{escape(contact.id)}/update" method="post"></form><input form="edit-{escape(contact.id)}" name="name" value="{escape(contact.name)}" aria-label="Contact name" required><input form="edit-{escape(contact.id)}" type="hidden" name="campaign_id" value="{escape(campaign.id)}"></td>
              <td><input form="edit-{escape(contact.id)}" name="phone" value="{escape(contact.phone)}" aria-label="Contact phone" required></td>
              <td><select form="edit-{escape(contact.id)}" name="status" aria-label="Contact status">{options}</select></td>
              <td class="num">{contact.attempts}</td><td class="num">{escape(contact.last_digit or "")}</td><td>{escape(format_dt(contact.next_call_at))}</td>
              <td><input form="edit-{escape(contact.id)}" name="notes" value="{escape(contact.notes or "")}" aria-label="Contact notes"></td>
              <td class="actions"><button form="edit-{escape(contact.id)}" type="submit">Save</button>
                <form action="contacts/{escape(contact.id)}/reset" method="post"><input type="hidden" name="campaign_id" value="{escape(campaign.id)}"><button type="submit">Reset</button></form>
                <form action="contacts/{escape(contact.id)}/delete" method="post"><input type="hidden" name="campaign_id" value="{escape(campaign.id)}"><button class="danger" type="submit">Delete</button></form>
              </td>
            </tr>""")
    rows_html = "\n".join(contact_rows) or '<tr><td colspan="8" class="empty">No contacts imported yet.</td></tr>'

    log_rows = []
    for item in logs:
        log_rows.append(f"""
            <tr><td>{escape(format_dt(item['created_at']))}</td><td>{escape(format_dt(item['completed_at']))}</td>
            <td>{escape(str(item['name'] or ""))}</td><td>{escape(str(item['phone'] or ""))}</td>
            <td>{escape(str(item['dial_input'] or ""))}</td><td>{escape(dial_normalization_label(str(item['dial_normalization'] or "")))}</td><td>{escape(str(item['dialed_number'] or ""))}</td>
            <td>{escape(str(item['caller_id_number'] or ""))}</td><td>{escape(str(item['sip_to'] or ""))}</td><td>{escape(str(item['sip_from'] or ""))}</td>
            <td>{escape(str(item['sip_route'] or ""))}</td><td>{escape(str(item['sip_last_response'] or ""))}</td><td>{escape(str(item['status'] or ""))}</td><td class="num">{escape(str(item['digit'] or ""))}</td>
            <td>{escape(str(item['amd_status'] or ""))}</td><td>{escape(str(item['transcript'] or ""))}</td><td>{escape(str(item['ai_decision'] or ""))}</td><td>{escape(str(item['ai_trace'] or ""))}</td><td>{recording_link(item['voice_recording'])}</td>
            <td>{escape(str(item['message'] or ""))}</td></tr>""")
    logs_html = "\n".join(log_rows) or '<tr><td colspan="20" class="empty">No call attempts logged yet.</td></tr>'

    event_rows = []
    for event in events:
        event_rows.append(f"<tr><td>{escape(format_dt(event.created_at))}</td><td>{escape(event.level)}</td><td>{escape(event.source)}</td><td>{escape(event.event_type)}</td><td>{escape(event.message)}</td><td>{escape(event.details or '')}</td></tr>")
    events_html = "\n".join(event_rows) or '<tr><td colspan="6" class="empty">No diagnostic events logged yet.</td></tr>'

    sip_html = "\n".join(f"<tr><td><code>{escape(line)}</code></td></tr>" for line in sip_lines) or '<tr><td class="empty">No SIP trace lines match this view.</td></tr>'
    limit_options = "".join(f'<option value="{limit}"{" selected" if trace_limit == limit else ""}>{escape(limit.upper())}</option>' for limit in TRACE_LIMITS)
    log_limit_options = "".join(f'<option value="{limit}"{" selected" if log_limit == limit else ""}>{escape(limit)}</option>' for limit in TABLE_LIMITS)
    event_limit_options = "".join(f'<option value="{limit}"{" selected" if event_limit == limit else ""}>{escape(limit)}</option>' for limit in TABLE_LIMITS)
    contact_refresh_options = "".join(
        f'<option value="{escape(value)}"{" selected" if contact_refresh == value else ""}>{escape(label)}</option>'
        for value, label in CONTACT_REFRESH_OPTIONS.items()
    )
    log_status_options = '<option value="">All statuses</option>' + "".join(
        f'<option value="{escape(option)}"{" selected" if log_status == option else ""}>{escape(option.replace("_", " ").title())}</option>'
        for option in ["queued", "originated", "failed", *STATUSES]
    )
    dial_normalization_options = "".join(
        f'<option value="{escape(value)}"{" selected" if campaign.dial_normalization == value else ""}>{escape(label)}</option>'
        for value, label in DIAL_NORMALIZATION_OPTIONS.items()
    )
    ai_provider_options = "".join(
        f'<option value="{escape(value)}"{" selected" if clean_ai_provider(campaign.ai_provider) == value else ""}>{escape(label)}</option>'
        for value, label in AI_PROVIDERS.items()
    )
    campaign_state = "Running" if campaign.enabled else "Stopped"
    state_css = "running" if campaign.enabled else "stopped"
    refreshed_at = format_dt(datetime.now(timezone.utc))
    next_retry = format_dt(status["next_retry_at"]) or "none"
    if not campaign.enabled:
        readiness_message = "Campaign is stopped; start it to allow the worker to place eligible calls."
        readiness_css = "muted"
    elif int(status["eligible_now"]):
        readiness_message = f'{status["eligible_now"]} contact(s) are eligible now; the worker can queue up to {campaign.max_calls_per_worker_tick} per tick.'
        readiness_css = "good"
    elif status["next_retry_at"]:
        readiness_message = f"No contacts are due right now; next retry is {next_retry}."
        readiness_css = "warn"
    elif not int(status["total_contacts"]):
        readiness_message = "No contacts exist in this campaign yet."
        readiness_css = "warn"
    elif int(status["maxed_out"]):
        readiness_message = f'{status["maxed_out"]} pending/no-response contact(s) have reached max attempts.'
        readiness_css = "warn"
    else:
        readiness_message = "No pending or no-response contacts are eligible; reset a contact or add a new pending contact to place more calls."
        readiness_css = "muted"
    readiness_html = f'<section class="readiness {readiness_css}"><strong>Dialer readiness</strong><span>{escape(readiness_message)}</span></section>'
    tabs = "".join([
        tab_link("Dashboard", "dashboard", tab, campaign.id),
        tab_link("Contacts", "contacts", tab, campaign.id, contact_refresh=contact_refresh),
        tab_link("Call Log", "logs", tab, campaign.id),
        tab_link("Asterisk SIP Trace", "sip", tab, campaign.id),
        tab_link("Diagnostics", "diagnostics", tab, campaign.id),
        tab_link("Settings", "settings", tab, campaign.id),
        tab_link("AI Flow", "ai", tab, campaign.id),
        tab_link("Voice Script", "voice", tab, campaign.id),
        tab_link("Campaigns", "campaigns", tab, campaign.id),
    ])
    command_bar = f"""
      <section class="command-bar">
        <div class="campaign-summary">
          <span class="eyebrow">Selected Campaign</span>
          <strong>{escape(campaign.name)}</strong>
          <span class="status-pill {state_css}">{campaign_state}</span>
        </div>
        <div class="command-actions">
          <form action="campaign/start" method="post"><input type="hidden" name="campaign_id" value="{escape(campaign.id)}"><button class="primary-action" type="submit">Start Campaign</button></form>
          <form action="campaign/stop" method="post"><input type="hidden" name="campaign_id" value="{escape(campaign.id)}"><button class="secondary" type="submit">Stop</button></form>
        </div>
        <div class="command-meta">
          <span>Window <strong>{escape(campaign.call_window_start)}-{escape(campaign.call_window_end)}</strong></span>
          <span>Last attempt <strong>{escape(format_dt(status["last_attempt_at"]) or "none")}</strong></span>
          <span>Eligible now <strong>{status["eligible_now"]}</strong></span>
          <span>Next retry <strong>{escape(next_retry)}</strong></span>
          <span>Refreshed <strong>{escape(refreshed_at)}</strong></span>
          <span>Prefix <strong>{escape(campaign.outbound_dial_prefix or "none")}</strong></span>
          <span>Format <strong>{escape(dial_normalization_label(campaign.dial_normalization))}</strong></span>
          <span>AI <strong>{escape("On" if campaign.ai_enabled else "Off")} / {escape(AI_PROVIDERS.get(clean_ai_provider(campaign.ai_provider), "Local"))}</strong></span>
        </div>
      </section>
    """
    export_bar = f"""
      <section class="export-bar">
        <a class="button secondary" href="template.csv">CSV Template</a>
        <a class="button secondary" href="{with_params("contacts/export.csv", campaign_id=campaign.id)}">Export Contacts</a>
        <a class="button secondary" href="{with_params("logs/export.csv", campaign_id=campaign.id, log_order=log_order, log_status=log_status, log_filter=log_filter)}">Export Logs</a>
        <a class="button secondary" href="{with_params("events/export.csv", campaign_id=campaign.id, event_order=event_order, event_filter=event_filter)}">Export Events</a>
        <a class="button secondary" href="{with_params("sip-trace/export.txt", campaign_id=campaign.id, trace_filter=trace_filter, trace_order=trace_order, trace_limit=trace_limit)}">Export Asterisk SIP Trace</a>
      </section>
    """
    msg_html = f'<p class="notice">{escape(message)}</p>' if message else ""
    last_attempt = format_dt(status["last_attempt_at"])
    attempt_summary = ", ".join(f"{escape(str(k))}: {int(v)}" for k, v in status["attempts"].items()) or "none"

    recent_log_rows = "".join(
        f"<tr><td>{escape(format_dt(item['created_at']))}</td><td>{escape(str(item['name'] or ''))}</td><td>{escape(str(item['status'] or ''))}</td><td>{escape(str(item['message'] or ''))}</td></tr>"
        for item in logs[:8]
    ) or '<tr><td colspan="4" class="empty">No recent call activity.</td></tr>'
    recent_event_rows = "".join(
        f"<tr><td>{escape(format_dt(event.created_at))}</td><td>{escape(event.event_type)}</td><td>{escape(event.message)}</td></tr>"
        for event in events[:8]
    ) or '<tr><td colspan="3" class="empty">No recent diagnostic events.</td></tr>'

    dashboard_section = f"""
      <section class="stats">{cards_html}</section>
      {readiness_html}
      <p class="meta">Last attempt: {escape(last_attempt or "none")} | Attempt statuses: {attempt_summary}</p>
      <section class="dashboard-grid">
        <div class="panel"><h2>Recent Calls</h2><div class="scroll"><table><thead><tr><th>Created</th><th>Name</th><th>Status</th><th>Message</th></tr></thead><tbody>{recent_log_rows}</tbody></table></div></div>
        <div class="panel"><h2>Recent Events</h2><div class="scroll"><table><thead><tr><th>Created</th><th>Type</th><th>Message</th></tr></thead><tbody>{recent_event_rows}</tbody></table></div></div>
      </section>"""

    settings_section = f"""
      <section class="grid2">
        <div class="panel"><h2>Caller ID and Dialing</h2><form class="settings" action="settings" method="post"><input type="hidden" name="campaign_id" value="{escape(campaign.id)}"><label>{field_label("Caller ID Name", "Text label sent with outbound calls where Avaya allows it.")}<input name="caller_id_name" value="{escape(campaign.caller_id_name)}"></label><label>{field_label("Caller ID Number", "Outbound ANI/caller ID number to present to called parties.")}<input name="caller_id_number" value="{escape(campaign.caller_id_number)}"></label><label>{field_label("Dial Prefix", "Digits prepended after the number-format rule runs, such as 9 for an outside line.")}<input name="outbound_dial_prefix" value="{escape(campaign.outbound_dial_prefix)}"></label><label>{field_label("Number Format", "Controls how the contact phone field is converted before the dial prefix is prepended.")}<select name="dial_normalization">{dial_normalization_options}</select></label><input type="hidden" name="call_window_start" value="{escape(campaign.call_window_start)}"><input type="hidden" name="call_window_end" value="{escape(campaign.call_window_end)}"><input type="hidden" name="timezone" value="{escape(campaign.timezone)}"><input type="hidden" name="max_attempts" value="{campaign.max_attempts}"><input type="hidden" name="retry_minutes" value="{campaign.retry_minutes}"><input type="hidden" name="max_calls_per_worker_tick" value="{campaign.max_calls_per_worker_tick}"><button type="submit">Save Caller ID</button></form></div>
        <div class="panel"><h2>Schedule and Limits</h2><form class="settings" action="settings" method="post"><input type="hidden" name="campaign_id" value="{escape(campaign.id)}"><input type="hidden" name="caller_id_name" value="{escape(campaign.caller_id_name)}"><input type="hidden" name="caller_id_number" value="{escape(campaign.caller_id_number)}"><input type="hidden" name="outbound_dial_prefix" value="{escape(campaign.outbound_dial_prefix)}"><input type="hidden" name="dial_normalization" value="{escape(campaign.dial_normalization)}"><label>{field_label("Call Window Start", "Earliest local time this campaign can place calls, HH:MM.")}<input name="call_window_start" value="{escape(campaign.call_window_start)}"></label><label>{field_label("Call Window End", "Latest local time this campaign can place calls, HH:MM.")}<input name="call_window_end" value="{escape(campaign.call_window_end)}"></label><label>{field_label("Timezone", "Timezone used to evaluate the call window.")}<input name="timezone" value="{escape(campaign.timezone)}"></label><label>{field_label("Max Attempts", "Maximum attempts per contact before the worker stops retrying.")}<input name="max_attempts" value="{campaign.max_attempts}"></label><label>{field_label("Retry Minutes", "Minutes to wait before a no-response contact can be tried again.")}<input name="retry_minutes" value="{campaign.retry_minutes}"></label><label>{field_label("Calls Per Tick", "Maximum contacts queued each worker cycle for this campaign.")}<input name="max_calls_per_worker_tick" value="{campaign.max_calls_per_worker_tick}"></label><button type="submit">Save Schedule</button></form></div>
      </section>"""

    campaigns_rows = "".join(f'<tr><td>{escape(item.name)}</td><td>{"Running" if item.enabled else "Stopped"}</td><td>{escape(item.call_window_start)}-{escape(item.call_window_end)}</td><td>{escape(item.caller_id_number)}</td><td><a class="button secondary" href="{with_params("./", campaign_id=item.id, tab="dashboard")}">Open</a></td></tr>' for item in all_campaigns)
    campaigns_section = f"""
      <section class="panel"><h2>Create Campaign</h2><form class="inline" action="campaigns/add" method="post"><label>{field_label("Campaign Name", "A separate calling project with its own contacts, logs, and dialing settings.")}<input name="name" placeholder="Campaign name" required></label><button type="submit">Add Campaign</button></form></section>
      <section><h2>Campaigns</h2><div class="scroll"><table><thead><tr><th>Name</th><th>Status</th><th>Window</th><th>Caller ID</th><th>Open</th></tr></thead><tbody>{campaigns_rows}</tbody></table></div></section>"""

    contacts_section = f"""
      <section class="panel"><h2>Add Contact</h2><form class="inline" action="contacts/add" method="post"><input type="hidden" name="campaign_id" value="{escape(campaign.id)}"><label>{field_label("Name", "Person or household name for this campaign contact.")}<input name="name" placeholder="Name" required></label><label>{field_label("Phone", "Phone number or internal extension to dial.")}<input name="phone" placeholder="Phone or extension" required></label><label>{field_label("Notes", "Optional notes visible only in this web UI and exports.")}<input name="notes" placeholder="Notes"></label><button type="submit">Add</button></form></section>
      <section class="panel table-toolbar" data-contact-refresh="{escape(contact_refresh)}">
        <h2>Contacts</h2>
        <form class="inline" action="./" method="get">
          <input type="hidden" name="campaign_id" value="{escape(campaign.id)}">
          <input type="hidden" name="tab" value="contacts">
          <label>{field_label("Auto Refresh", "Reloads the Contacts tab every selected interval. It pauses while a field is focused or changed.")}<select name="contact_refresh">{contact_refresh_options}</select></label>
          <button type="submit">Apply</button>
          <a class="button secondary" href="{with_params("./", campaign_id=campaign.id, tab="contacts", contact_refresh=contact_refresh)}">Refresh</a>
          <span class="table-timestamp">Last refreshed {escape(refreshed_at)}</span>
          <span id="contact-refresh-state" class="refresh-status"></span>
        </form>
      </section>
      <section><div class="scroll"><table><thead><tr><th>Name</th><th>Phone</th><th>Status</th><th>Attempts</th><th>Digit</th><th>Next Call</th><th>Notes</th><th>Actions</th></tr></thead><tbody>{rows_html}</tbody></table></div></section>"""

    logs_section = f"""
      <section class="panel table-toolbar">
        <h2>Call Log</h2>
        <form class="inline" action="./" method="get">
          <input type="hidden" name="campaign_id" value="{escape(campaign.id)}">
          <input type="hidden" name="tab" value="logs">
          <label>{field_label("Filter", "Search dialed number, SIP headers, transcript, or message.")}<input name="log_filter" value="{escape(log_filter)}" placeholder="Search logs"></label>
          <label>{field_label("Status", "Limit the call log to one call status.")}<select name="log_status">{log_status_options}</select></label>
          <label>{field_label("Sort", "Sort call attempts by created time.")}<select name="log_order"><option value="newest"{" selected" if log_order == "newest" else ""}>Newest first</option><option value="oldest"{" selected" if log_order == "oldest" else ""}>Oldest first</option></select></label>
          <label>{field_label("Rows", "Number of call attempts to show.")}<select name="log_limit">{log_limit_options}</select></label>
          <button type="submit">Apply</button>
          <a class="button secondary" href="{with_params("./", campaign_id=campaign.id, tab="logs", log_filter=log_filter, log_status=log_status, log_order=log_order, log_limit=log_limit)}">Refresh</a>
          <span class="table-timestamp">Last refreshed {escape(refreshed_at)}</span>
        </form>
      </section>
      <section><div class="scroll"><table><thead><tr><th>Created</th><th>Completed</th><th>Name</th><th>Contact Phone</th><th>Dial Input</th><th>Format</th><th>Dialed</th><th>Caller ID</th><th>SIP To</th><th>SIP From</th><th>SIP Route</th><th>Last SIP Response</th><th>Status</th><th>Digit</th><th>AMD</th><th>Transcript</th><th>AI Decision</th><th>AI Trace</th><th>Recording</th><th>Message</th></tr></thead><tbody>{logs_html}</tbody></table></div></section>
    """
    voice_section = f"""
      <section class="panel">
        <h2>Voice Script</h2>
        <form class="script-form" action="voice-script" method="post">
          <input type="hidden" name="campaign_id" value="{escape(campaign.id)}">
          <label>{field_label("Intro Script", "Main message played to a human answer. You can use {contact_name}.")}<textarea name="intro_script" rows="5">{escape(script_value(campaign, "intro_script"))}</textarea></label>
          <label>{field_label("Voicemail Script", "Message left when AMD classifies the answer as voicemail.")}<textarea name="voicemail_script" rows="3">{escape(script_value(campaign, "voicemail_script"))}</textarea></label>
          <label>{field_label("Voice Answer Prompt", "Prompt played when no DTMF digit is pressed before recording a spoken answer.")}<textarea name="voice_prompt_script" rows="2">{escape(script_value(campaign, "voice_prompt_script"))}</textarea></label>
          <label>{field_label("Thank You: Attending", "Played after digit 1 or a transcript classified as yes/attending.")}<textarea name="thanks_attending_script" rows="2">{escape(script_value(campaign, "thanks_attending_script"))}</textarea></label>
          <label>{field_label("Thank You: Not Attending", "Played after digit 2 or a transcript classified as no/not attending.")}<textarea name="thanks_not_attending_script" rows="2">{escape(script_value(campaign, "thanks_not_attending_script"))}</textarea></label>
          <label>{field_label("Thank You: Unsure", "Played after digit 3 or a transcript classified as maybe/unsure.")}<textarea name="thanks_unsure_script" rows="2">{escape(script_value(campaign, "thanks_unsure_script"))}</textarea></label>
          <label>{field_label("Thank You: Callback", "Played after digit 9 or a transcript classified as call me back.")}<textarea name="thanks_callback_script" rows="2">{escape(script_value(campaign, "thanks_callback_script"))}</textarea></label>
          <label>{field_label("No Response", "Played when neither DTMF nor speech classification gets a usable answer.")}<textarea name="no_response_script" rows="2">{escape(script_value(campaign, "no_response_script"))}</textarea></label>
          <button type="submit">Save Voice Script</button>
        </form>
        <p class="meta">Asterisk regenerates prompt audio when the text changes. Current speech-to-text still needs a transcriber command configured before spoken answers become reliable transcripts.</p>
      </section>"""
    ai_notes = escape(campaign.ai_builder_notes or "No AI builder conversation yet.")
    ai_section = f"""
      <section class="grid2">
        <div class="panel">
          <h2>AI Call Brain</h2>
          <form class="settings" action="ai/settings" method="post">
            <input type="hidden" name="campaign_id" value="{escape(campaign.id)}">
            <label>{field_label("AI Enabled", "When enabled, answered calls use the AI decision loop before and after prompts.")}<select name="ai_enabled"><option value="1"{" selected" if campaign.ai_enabled else ""}>On</option><option value="0"{" selected" if not campaign.ai_enabled else ""}>Off</option></select></label>
            <label>{field_label("Provider", "Local uses built-in rules. Flowise sends call state to the configured chatflow.")}<select name="ai_provider">{ai_provider_options}</select></label>
            <label>{field_label("Observe Milliseconds", "Set 0 for fast-start speech immediately after answer. Higher values record/transcribe before the first prompt for voicemail detection.")}<input name="ai_observe_ms" value="{campaign.ai_observe_ms}"></label>
            <label>{field_label("Listen Milliseconds", "Speech listen window after each AI prompt.")}<input name="ai_listen_ms" value="{campaign.ai_listen_ms}"></label>
            <label>{field_label("Max Turns", "Maximum AI listen/speak cycles before no-response handling.")}<input name="ai_max_turns" value="{campaign.ai_max_turns}"></label>
            <label>{field_label("Flowise URL", "Prediction endpoint, usually http://gaid:3000/api/v1/prediction.")}<input name="flowise_api_url" value="{escape(campaign.flowise_api_url or '')}"></label>
            <label>{field_label("Flowise Chatflow ID", "Flowise chatflow ID used for call-brain decisions.")}<input name="flowise_chatflow_id" value="{escape(campaign.flowise_chatflow_id or '')}"></label>
            <label>{field_label("Flowise API Key", "Optional bearer token for the Flowise prediction API.")}<input type="password" name="flowise_api_key" value="{escape(campaign.flowise_api_key or '')}"></label>
            <label>{field_label("Flowise Username", "Optional HTTP basic username if Flowise basic auth is enabled.")}<input name="flowise_username" value="{escape(campaign.flowise_username or '')}"></label>
            <label>{field_label("Flowise Password", "Optional HTTP basic password if Flowise basic auth is enabled.")}<input type="password" name="flowise_password" value="{escape(campaign.flowise_password or '')}"></label>
            <label class="wide">{field_label("Event Context", "Facts the AI should know about the birthday, host, venue, RSVP rules, and caller identity.")}<textarea name="ai_event_context" rows="7">{escape(campaign.ai_event_context or '')}</textarea></label>
            <label class="wide">{field_label("System Prompt", "Behavior rules for how the AI should decide when to speak, listen, mark RSVP, or leave voicemail.")}<textarea name="ai_system_prompt" rows="7">{escape(campaign.ai_system_prompt or '')}</textarea></label>
            <button type="submit">Save AI Flow</button>
          </form>
          <form class="inline" action="ai/test-flowise" method="post">
            <input type="hidden" name="campaign_id" value="{escape(campaign.id)}">
            <button class="secondary" type="submit">Test Flowise</button>
          </form>
        </div>
        <div class="panel">
          <h2>AI Flow Builder</h2>
          <div class="chat-window"><pre>{ai_notes}</pre></div>
          <form class="builder-form" action="ai/builder-chat" method="post">
            <input type="hidden" name="campaign_id" value="{escape(campaign.id)}">
            <label>{field_label("Builder Message", "Tell the builder what the event is, how the caller should sound, or how an edge case should work.")}<textarea name="builder_message" rows="7" placeholder="Mom is turning 80. The party is at..."></textarea></label>
            <label class="checkline"><input type="checkbox" name="apply_to_context" value="1"> Add this to event context</label>
            <button type="submit">Send To Builder</button>
          </form>
        </div>
      </section>"""
    diagnostics_section = f"""
      <section class="panel table-toolbar">
        <h2>Diagnostic Events</h2>
        <form class="inline" action="./" method="get">
          <input type="hidden" name="campaign_id" value="{escape(campaign.id)}">
          <input type="hidden" name="tab" value="diagnostics">
          <label>{field_label("Filter", "Search event type, source, message, or details.")}<input name="event_filter" value="{escape(event_filter)}" placeholder="Search events"></label>
          <label>{field_label("Sort", "Sort diagnostic events by created time.")}<select name="event_order"><option value="newest"{" selected" if event_order == "newest" else ""}>Newest first</option><option value="oldest"{" selected" if event_order == "oldest" else ""}>Oldest first</option></select></label>
          <label>{field_label("Rows", "Number of diagnostic events to show.")}<select name="event_limit">{event_limit_options}</select></label>
          <button type="submit">Apply</button>
          <a class="button secondary" href="{with_params("./", campaign_id=campaign.id, tab="diagnostics", event_filter=event_filter, event_order=event_order, event_limit=event_limit)}">Refresh</a>
          <span class="table-timestamp">Last refreshed {escape(refreshed_at)}</span>
        </form>
      </section>
      <section><div class="scroll"><table><thead><tr><th>Created</th><th>Level</th><th>Source</th><th>Type</th><th>Message</th><th>Details</th></tr></thead><tbody>{events_html}</tbody></table></div></section>
    """
    sip_section = f"""
      <section class="panel"><h2>Asterisk SIP Trace</h2><form class="inline" action="./" method="get"><input type="hidden" name="campaign_id" value="{escape(campaign.id)}"><input type="hidden" name="tab" value="sip"><label>{field_label("Filter", "Show only Asterisk-side SIP trace lines containing this text.")}<input name="trace_filter" value="{escape(trace_filter)}" placeholder="Filter"></label><label>{field_label("Sort Order", "Choose whether newest or oldest Asterisk SIP trace lines appear first.")}<select name="trace_order"><option value="newest"{" selected" if trace_order == "newest" else ""}>Newest first</option><option value="oldest"{" selected" if trace_order == "oldest" else ""}>Oldest first</option></select></label><label>{field_label("Rows", "Number of Asterisk SIP trace lines to display.")}<select name="trace_limit">{limit_options}</select></label><button type="submit">Apply</button><a class="button secondary" href="{with_params("./", campaign_id=campaign.id, tab="sip", trace_filter=trace_filter, trace_order=trace_order, trace_limit=trace_limit)}">Refresh</a><span class="table-timestamp">Last refreshed {escape(refreshed_at)}</span></form><form class="inline" action="sip-trace/clear" method="post"><input type="hidden" name="campaign_id" value="{escape(campaign.id)}"><button class="secondary" type="submit" title="Hide currently visible older Asterisk SIP trace lines from this UI.">Clear View</button></form></section>
      <section><div class="scroll"><table><thead><tr><th>Recent Asterisk SIP Messages</th></tr></thead><tbody>{sip_html}</tbody></table></div></section>"""

    sections = {
        "dashboard": dashboard_section,
        "campaigns": campaigns_section,
        "contacts": contacts_section,
        "logs": logs_section,
        "ai": ai_section,
        "voice": voice_section,
        "diagnostics": diagnostics_section,
        "settings": settings_section,
        "sip": sip_section,
    }
    body_section = sections.get(tab, dashboard_section)
    auto_refresh_script = ""
    if tab == "contacts" and contact_refresh != "0":
        auto_refresh_script = """
<script>
(() => {
  const intervalSeconds = CONTACT_REFRESH_SECONDS;
  const campaignId = CONTACT_REFRESH_CAMPAIGN;
  const state = document.getElementById("contact-refresh-state");
  let remaining = intervalSeconds;
  let dirty = false;
  const editableSelector = "input, textarea, select";
  const editing = () => {
    const active = document.activeElement;
    return dirty || !!(active && active.matches && active.matches(editableSelector));
  };
  const render = () => {
    if (!state) return;
    state.textContent = editing() ? "Auto-refresh paused while editing" : `Auto-refresh in ${remaining}s`;
  };
  document.addEventListener("input", (event) => {
    if (event.target.matches && event.target.matches(editableSelector)) dirty = true;
    render();
  }, true);
  document.addEventListener("change", (event) => {
    if (event.target.matches && event.target.matches(editableSelector)) dirty = true;
    render();
  }, true);
  setInterval(() => {
    if (editing()) {
      remaining = intervalSeconds;
      render();
      return;
    }
    remaining -= 1;
    if (remaining <= 0) {
      const url = new URL(window.location.href);
      url.searchParams.set("campaign_id", campaignId);
      url.searchParams.set("tab", "contacts");
      url.searchParams.set("contact_refresh", String(intervalSeconds));
      window.location.replace(url.toString());
      return;
    }
    render();
  }, 1000);
  render();
})();
</script>
""".replace("CONTACT_REFRESH_SECONDS", contact_refresh).replace("CONTACT_REFRESH_CAMPAIGN", json.dumps(campaign.id))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Devin's Out Caller</title>
<style>
:root {{ color-scheme: light; font-family: Arial, system-ui, sans-serif; }}
body {{ margin:0; background:#f6f7f9; color:#1b1f24; }}
header {{ background:#263238; color:white; padding:16px 28px; display:flex; gap:16px; align-items:end; justify-content:space-between; flex-wrap:wrap; }}
h1 {{ font-size:22px; margin:0; letter-spacing:0; }}
main {{ padding:18px 28px 40px; max-width:1500px; margin:0 auto; }}
h2 {{ font-size:17px; margin:0 0 12px; }}
section {{ margin-top:16px; }}
.panel,.bar,.command-bar,.export-bar {{ background:white; border:1px solid #d7dde4; border-radius:6px; padding:14px; }}
.panel,section,form,label,.grid2 > *,.dashboard-grid > * {{ min-width:0; }}
.bar,.actions,.inline,.command-actions,.command-meta,.export-bar {{ display:flex; gap:8px; flex-wrap:wrap; align-items:end; }}
.inline label {{ flex:1 1 190px; }}
.inline button,.inline .button {{ flex:0 0 auto; }}
.command-bar {{ display:grid; grid-template-columns:minmax(220px, 1fr) auto minmax(260px, 1.3fr); gap:14px; align-items:center; border-left:5px solid #30475e; }}
.campaign-summary {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.campaign-summary strong {{ font-size:20px; }}
.eyebrow {{ color:#687386; font-size:12px; text-transform:uppercase; }}
.status-pill {{ border-radius:999px; padding:4px 9px; font-size:12px; font-weight:bold; }}
.status-pill.running {{ color:#0f6b3d; background:#dff3e8; }}
.status-pill.stopped {{ color:#687386; background:#eef2f6; }}
.command-meta span {{ color:#5e6978; font-size:13px; }}
.command-meta strong {{ color:#1b1f24; }}
.export-bar {{ justify-content:flex-start; }}
.tabs {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:16px; }}
.tab {{ padding:9px 12px; border:1px solid #c8d1dc; color:#263238; background:white; border-radius:5px; text-decoration:none; }}
.tab.active {{ background:#30475e; color:white; border-color:#30475e; }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }}
.stat {{ background:white; border:1px solid #d7dde4; border-radius:6px; padding:14px; }}
.stat span {{ display:block; font-size:12px; color:#5e6978; text-transform:uppercase; }}
.stat strong {{ display:block; font-size:24px; margin-top:6px; }}
.good strong {{ color:#147a46; }} .bad strong {{ color:#a43737; }} .warn strong {{ color:#9a6500; }} .muted strong {{ color:#687386; }}
input,select,button,textarea {{ font:inherit; font-size:14px; }}
input,select,textarea {{ box-sizing:border-box; width:100%; max-width:100%; min-width:0; padding:7px 8px; border:1px solid #bac4cf; border-radius:4px; background:white; }}
input[type="checkbox"],input[type="radio"] {{ width:auto; }}
textarea {{ min-height:44px; resize:vertical; line-height:1.35; }}
button,.button {{ border:1px solid #5d7187; background:#30475e; color:white; border-radius:4px; padding:8px 11px; text-decoration:none; cursor:pointer; white-space:nowrap; }}
.primary-action {{ background:#147a46; border-color:#147a46; font-weight:bold; }}
.secondary,.button.secondary {{ background:#eef2f6; color:#263238; border-color:#c6d0db; }}
.danger {{ background:#8b2d2d; border-color:#8b2d2d; }}
.help {{ width:20px; height:20px; padding:0; border-radius:50%; background:#eef2f6; color:#263238; border-color:#b8c4d0; font-size:12px; line-height:18px; }}
.notice {{ background:#e8f5ed; border:1px solid #b9e1c8; padding:10px 12px; border-radius:6px; }}
.readiness {{ background:white; border:1px solid #d7dde4; border-left:5px solid #687386; border-radius:6px; padding:12px 14px; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.readiness strong {{ font-size:13px; text-transform:uppercase; color:#4f5b68; }}
.readiness.good {{ border-left-color:#147a46; }} .readiness.warn {{ border-left-color:#b57a00; }} .readiness.muted {{ border-left-color:#9aa6b2; }}
.scroll {{ overflow-x:auto; max-width:100%; }}
table {{ width:100%; border-collapse:collapse; background:white; border:1px solid #d7dde4; }}
th,td {{ border-bottom:1px solid #e2e7ee; padding:9px; text-align:left; vertical-align:middle; }}
th {{ font-size:12px; text-transform:uppercase; color:#5e6978; background:#f0f3f6; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.empty {{ text-align:center; color:#687386; padding:26px; }}
.meta {{ color:#687386; font-size:13px; margin-top:10px; }}
.grid2,.dashboard-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:14px; }}
.settings {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; }}
.script-form,.builder-form {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
.script-form label:first-of-type,.wide {{ grid-column:1 / -1; }}
.chat-window {{ min-height:280px; max-height:480px; overflow:auto; background:#f8fafb; border:1px solid #d7dde4; border-radius:5px; padding:10px; }}
.chat-window pre {{ margin:0; white-space:pre-wrap; font-family:Consolas,monospace; font-size:13px; line-height:1.4; }}
.checkline {{ display:flex; align-items:center; gap:8px; }}
.table-toolbar form {{ margin-top:8px; }}
.table-timestamp {{ color:#687386; font-size:13px; padding:8px 0; }}
.refresh-status {{ color:#4f5b68; font-size:13px; padding:8px 0; font-variant-numeric:tabular-nums; }}
label {{ display:grid; gap:5px; color:#4f5b68; font-size:13px; }}
label span {{ display:flex; gap:5px; align-items:center; }}
code {{ white-space:pre-wrap; font-family:Consolas,monospace; font-size:12px; }}
@media (max-width:900px) {{ main {{ padding:18px 12px; }} table {{ min-width:980px; }} .script-form,.builder-form,.command-bar {{ grid-template-columns:1fr; }} }}
</style></head>
<body><header><h1>Devin's Out Caller</h1><form action="./" method="get" class="inline"><label>{field_label("Campaign", "Select which campaign's contacts, settings, and logs are shown.")}<select name="campaign_id">{campaign_options}</select></label><input type="hidden" name="tab" value="{escape(tab)}"><button class="secondary" type="submit">Open</button></form></header>
<main>{msg_html}{command_bar}<div class="tabs">{tabs}</div>{export_bar}{body_section}</main>{auto_refresh_script}</body></html>"""


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def admin_home(
    db: Session = Depends(get_db),
    campaign_id: str | None = None,
    tab: str = "dashboard",
    message: str = "",
    log_order: str = "newest",
    log_limit: str = "100",
    log_filter: str = "",
    log_status: str = "",
    event_order: str = "newest",
    event_limit: str = "100",
    event_filter: str = "",
    trace_order: str = "newest",
    trace_limit: str = "100",
    trace_filter: str = "",
    contact_refresh: str = "10",
) -> str:
    return render_admin(
        db,
        campaign_id,
        tab,
        message,
        log_order,
        log_limit,
        log_filter,
        log_status,
        event_order,
        event_limit,
        event_filter,
        trace_order,
        trace_limit,
        trace_filter,
        contact_refresh,
    )


@app.get("/status")
def status(campaign_id: str | None = None, db: Session = Depends(get_db)) -> dict[str, object]:
    campaign = ensure_campaign(db, campaign_id)
    return get_status(db, campaign.id)


@app.get("/template.csv")
def template_csv() -> Response:
    return Response(CSV_TEMPLATE, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="out-caller-template.csv"'})


@app.post("/campaigns/add")
def add_campaign(request: Request, name: str = Form(...), db: Session = Depends(get_db)):
    campaign = Campaign(name=name.strip(), caller_id_name="Devin's Out Caller")
    db.add(campaign)
    db.commit()
    add_event(db, "campaign_created", f"Campaign created: {campaign.name}", campaign_id=campaign.id)
    return see_other(request, "Campaign created", campaign_id=campaign.id, tab="dashboard")


@app.post("/campaign/start")
def start_campaign(request: Request, campaign_id: str = Form(DEFAULT_CAMPAIGN_ID), db: Session = Depends(get_db)):
    campaign = ensure_campaign(db, campaign_id)
    campaign.enabled = 1
    db.commit()
    add_event(db, "campaign_started", "Campaign was started from the web UI.", campaign_id=campaign.id)
    return see_other(request, "Campaign started", campaign_id=campaign.id)


@app.post("/campaign/stop")
def stop_campaign(request: Request, campaign_id: str = Form(DEFAULT_CAMPAIGN_ID), db: Session = Depends(get_db)):
    campaign = ensure_campaign(db, campaign_id)
    campaign.enabled = 0
    db.commit()
    add_event(db, "campaign_stopped", "Campaign was stopped from the web UI.", campaign_id=campaign.id)
    return see_other(request, "Campaign stopped", campaign_id=campaign.id)


@app.post("/settings")
def update_settings(
    request: Request,
    campaign_id: str = Form(DEFAULT_CAMPAIGN_ID),
    caller_id_name: str = Form(""),
    caller_id_number: str = Form(""),
    outbound_dial_prefix: str = Form(""),
    dial_normalization: str = Form("nanp_1"),
    call_window_start: str = Form("10:00"),
    call_window_end: str = Form("19:30"),
    timezone: str = Form("America/New_York"),
    max_attempts: str = Form("3"),
    retry_minutes: str = Form("240"),
    max_calls_per_worker_tick: str = Form("1"),
    db: Session = Depends(get_db),
):
    if not re.match(r"^\d{2}:\d{2}$", call_window_start.strip()) or not re.match(r"^\d{2}:\d{2}$", call_window_end.strip()):
        raise HTTPException(status_code=400, detail="call window values must be HH:MM")
    campaign = ensure_campaign(db, campaign_id)
    campaign.caller_id_name = caller_id_name.strip() or "Devin's Out Caller"
    campaign.caller_id_number = caller_id_number.strip()
    campaign.outbound_dial_prefix = "".join(ch for ch in outbound_dial_prefix.strip() if ch.isdigit() or ch in "*#")
    campaign.dial_normalization = clean_dial_normalization(dial_normalization)
    campaign.call_window_start = call_window_start.strip()
    campaign.call_window_end = call_window_end.strip()
    campaign.timezone = timezone.strip() or "America/New_York"
    for attr, value in [("max_attempts", max_attempts), ("retry_minutes", retry_minutes), ("max_calls_per_worker_tick", max_calls_per_worker_tick)]:
        try:
            parsed = int(value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{attr} must be a number") from None
        if parsed < 1:
            raise HTTPException(status_code=400, detail=f"{attr} must be at least 1")
        setattr(campaign, attr, parsed)
    campaign.updated_at = now_utc()
    db.commit()
    add_event(
        db,
        "settings_saved",
        "Campaign settings were saved from the web UI.",
        campaign_id=campaign.id,
        details=(
            f"caller_id={campaign.caller_id_name} <{campaign.caller_id_number}> "
            f"prefix={campaign.outbound_dial_prefix} number_format={campaign.dial_normalization} "
            f"window={campaign.call_window_start}-{campaign.call_window_end} timezone={campaign.timezone}"
        ),
    )
    return see_other(request, "Settings saved", campaign_id=campaign.id)


@app.post("/voice-script")
def update_voice_script(
    request: Request,
    campaign_id: str = Form(DEFAULT_CAMPAIGN_ID),
    intro_script: str = Form(""),
    voicemail_script: str = Form(""),
    voice_prompt_script: str = Form(""),
    thanks_attending_script: str = Form(""),
    thanks_not_attending_script: str = Form(""),
    thanks_unsure_script: str = Form(""),
    thanks_callback_script: str = Form(""),
    no_response_script: str = Form(""),
    db: Session = Depends(get_db),
):
    campaign = ensure_campaign(db, campaign_id)
    values = {
        "intro_script": intro_script,
        "voicemail_script": voicemail_script,
        "voice_prompt_script": voice_prompt_script,
        "thanks_attending_script": thanks_attending_script,
        "thanks_not_attending_script": thanks_not_attending_script,
        "thanks_unsure_script": thanks_unsure_script,
        "thanks_callback_script": thanks_callback_script,
        "no_response_script": no_response_script,
    }
    for field, value in values.items():
        setattr(campaign, field, value.strip() or SCRIPT_FIELDS[field])
    campaign.updated_at = now_utc()
    db.commit()
    add_event(db, "voice_script_saved", "Campaign voice script was saved from the web UI.", campaign_id=campaign.id)
    return see_other(request, "Voice script saved", campaign_id=campaign.id, tab="voice")


@app.post("/ai/settings")
def update_ai_settings(
    request: Request,
    campaign_id: str = Form(DEFAULT_CAMPAIGN_ID),
    ai_enabled: str = Form("0"),
    ai_provider: str = Form("local"),
    ai_observe_ms: str = Form("0"),
    ai_listen_ms: str = Form("7000"),
    ai_max_turns: str = Form("3"),
    ai_event_context: str = Form(""),
    ai_system_prompt: str = Form(""),
    flowise_api_url: str = Form(""),
    flowise_chatflow_id: str = Form(""),
    flowise_api_key: str = Form(""),
    flowise_username: str = Form(""),
    flowise_password: str = Form(""),
    db: Session = Depends(get_db),
):
    campaign = ensure_campaign(db, campaign_id)
    campaign.ai_enabled = truthy_form(ai_enabled)
    campaign.ai_provider = clean_ai_provider(ai_provider)
    campaign.ai_observe_ms = clamp_int(ai_observe_ms, 0, 0, 15000)
    campaign.ai_listen_ms = clamp_int(ai_listen_ms, 7000, 1000, 20000)
    campaign.ai_max_turns = clamp_int(ai_max_turns, 3, 1, 8)
    campaign.ai_event_context = ai_event_context.strip() or DEFAULT_AI_EVENT_CONTEXT
    campaign.ai_system_prompt = ai_system_prompt.strip() or DEFAULT_AI_SYSTEM_PROMPT
    campaign.flowise_api_url = flowise_api_url.strip() or "http://gaid:3000/api/v1/prediction"
    campaign.flowise_chatflow_id = flowise_chatflow_id.strip()
    campaign.flowise_api_key = flowise_api_key.strip()
    campaign.flowise_username = flowise_username.strip()
    campaign.flowise_password = flowise_password.strip()
    campaign.updated_at = now_utc()
    db.commit()
    add_event(
        db,
        "ai_settings_saved",
        "AI call-flow settings were saved from the web UI.",
        campaign_id=campaign.id,
        details=f"enabled={campaign.ai_enabled} provider={campaign.ai_provider} observe_ms={campaign.ai_observe_ms} listen_ms={campaign.ai_listen_ms} max_turns={campaign.ai_max_turns} flowise_url={campaign.flowise_api_url} chatflow_id={campaign.flowise_chatflow_id}",
    )
    return see_other(request, "AI flow saved", campaign_id=campaign.id, tab="ai")


@app.post("/ai/test-flowise")
def test_flowise(request: Request, campaign_id: str = Form(DEFAULT_CAMPAIGN_ID), db: Session = Depends(get_db)):
    campaign = ensure_campaign(db, campaign_id)
    payload = {"stage": "answer_observed", "answer_class": "human", "transcript": "hello", "turn": 0}
    decision, error = call_flowise(campaign, None, payload)
    if decision:
        add_event(db, "flowise_test_ok", "Flowise test returned an AI decision.", campaign_id=campaign.id, details=compact_json(decision))
        return see_other(request, "Flowise test OK", campaign_id=campaign.id, tab="ai")
    add_event(db, "flowise_test_failed", "Flowise test failed.", level="error", campaign_id=campaign.id, details=error)
    return see_other(request, f"Flowise test failed: {error[:140]}", campaign_id=campaign.id, tab="ai")


@app.post("/ai/builder-chat")
def ai_builder_chat(
    request: Request,
    campaign_id: str = Form(DEFAULT_CAMPAIGN_ID),
    builder_message: str = Form(""),
    apply_to_context: str = Form("0"),
    db: Session = Depends(get_db),
):
    campaign = ensure_campaign(db, campaign_id)
    message = builder_message.strip()
    if not message:
        return see_other(request, "Builder message was empty", campaign_id=campaign.id, tab="ai")
    if truthy_form(apply_to_context):
        current_context = (campaign.ai_event_context or DEFAULT_AI_EVENT_CONTEXT).strip()
        campaign.ai_event_context = f"{current_context}\n\n{message}".strip()
    suggestion = (
        "Builder: I added this as call-flow knowledge. The runtime will listen first, classify the answer, "
        "ask for RSVP only after a human response, and only mark yes/no/unsure/callback when the transcript or DTMF is clear. "
        "For Flowise, build the chatflow to return JSON with action, text, digit, status, reason, listen_ms, and hangup_after."
    )
    timestamp = format_dt(now_utc())
    entry = f"[{timestamp}] You: {message}\n[{timestamp}] {suggestion}"
    campaign.ai_builder_notes = ((campaign.ai_builder_notes or "").strip() + "\n\n" + entry).strip()
    campaign.updated_at = now_utc()
    db.commit()
    add_event(db, "ai_builder_chat", "AI builder message saved.", campaign_id=campaign.id, details=message[:500])
    return see_other(request, "Builder message saved", campaign_id=campaign.id, tab="ai")


@app.get("/agi/campaign/{campaign_id}/script")
def agi_campaign_script(campaign_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    campaign = ensure_campaign(db, campaign_id)
    data = {field: script_value(campaign, field) for field in SCRIPT_FIELDS}
    data.update(
        {
            "_ai_enabled": str(campaign.ai_enabled),
            "_ai_provider": clean_ai_provider(campaign.ai_provider),
            "_ai_observe_ms": str(campaign.ai_observe_ms),
            "_ai_listen_ms": str(campaign.ai_listen_ms),
            "_ai_max_turns": str(campaign.ai_max_turns),
        }
    )
    return data


@app.post("/agi/decision")
def agi_decision(payload: dict[str, object], db: Session = Depends(get_db)) -> dict[str, object]:
    campaign_id = str(payload.get("campaign_id") or DEFAULT_CAMPAIGN_ID)
    contact_id = str(payload.get("contact_id") or "")
    attempt_id = str(payload.get("attempt_id") or "")
    campaign = ensure_campaign(db, campaign_id)
    contact = db.get(Contact, contact_id) if contact_id else None
    attempt = db.get(CallAttempt, attempt_id) if attempt_id else None

    if not campaign.ai_enabled:
        decision = normalize_ai_decision({"action": "legacy", "reason": "AI disabled for this campaign", "source": "local"})
    else:
        decision = None
        flowise_error = ""
        if clean_ai_provider(campaign.ai_provider) == "flowise":
            decision, flowise_error = call_flowise(campaign, contact, payload)
        if not decision:
            decision = local_ai_decision(campaign, contact, payload, flowise_error)

    decision["listen_ms"] = clamp_int(decision.get("listen_ms"), campaign.ai_listen_ms or 7000, 1000, 20000)
    decision["max_turns"] = campaign.ai_max_turns or 3
    decision["observe_ms"] = clamp_int(campaign.ai_observe_ms, 0, 0, 15000)
    append_ai_trace(db, attempt, payload, decision)
    add_event(
        db,
        "ai_decision",
        f"AI decision: {decision.get('action')}",
        campaign_id=campaign.id,
        details=f"attempt_id={attempt_id} contact_id={contact_id} payload={compact_json(payload)} decision={compact_json(decision)}",
    )
    return decision


@app.post("/contacts/import")
async def import_contacts(request: Request, campaign_id: str = Form(DEFAULT_CAMPAIGN_ID), file: UploadFile = File(...), db: Session = Depends(get_db)):
    ensure_campaign(db, campaign_id)
    result = import_csv((await file.read()).decode("utf-8-sig"), db, campaign_id)
    return see_other(request, f"Imported {result['imported']}, updated {result['updated']}, skipped {result['skipped']}", campaign_id=campaign_id, tab="contacts")


@app.post("/contacts/add")
def add_contact(request: Request, campaign_id: str = Form(DEFAULT_CAMPAIGN_ID), name: str = Form(...), phone: str = Form(...), notes: str = Form(""), db: Session = Depends(get_db)):
    db.add(Contact(campaign_id=campaign_id, name=name.strip(), phone=phone.strip(), notes=notes.strip() or None))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return see_other(request, "Phone number already exists in this campaign", campaign_id=campaign_id, tab="contacts")
    return see_other(request, "Contact added", campaign_id=campaign_id, tab="contacts")


@app.post("/contacts/{contact_id}/update")
def update_contact(request: Request, contact_id: str, campaign_id: str = Form(DEFAULT_CAMPAIGN_ID), name: str = Form(...), phone: str = Form(...), status: str = Form(...), notes: str = Form(""), db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="contact not found")
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    contact.name = name.strip()
    contact.phone = phone.strip()
    contact.status = status
    contact.notes = notes.strip() or None
    contact.updated_at = now_utc()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return see_other(request, "Phone number already exists in this campaign", campaign_id=campaign_id, tab="contacts")
    return see_other(request, "Contact saved", campaign_id=campaign_id, tab="contacts")


@app.post("/contacts/{contact_id}/reset")
def reset_contact(request: Request, contact_id: str, campaign_id: str = Form(DEFAULT_CAMPAIGN_ID), db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="contact not found")
    contact.status = "pending"
    contact.attempts = 0
    contact.last_digit = None
    contact.next_call_at = None
    contact.updated_at = now_utc()
    db.commit()
    return see_other(request, "Contact reset", campaign_id=campaign_id, tab="contacts")


@app.post("/contacts/{contact_id}/delete")
def delete_contact(request: Request, contact_id: str, campaign_id: str = Form(DEFAULT_CAMPAIGN_ID), db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if contact:
        db.query(CallAttempt).filter(CallAttempt.contact_id == contact_id).delete()
        db.delete(contact)
        db.commit()
    return see_other(request, "Contact deleted", campaign_id=campaign_id, tab="contacts")


@app.get("/contacts")
def list_contacts(campaign_id: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    campaign = ensure_campaign(db, campaign_id)
    return [contact_dict(contact) for contact in db.scalars(select(Contact).where(Contact.campaign_id == campaign.id).order_by(Contact.created_at)).all()]


@app.get("/contacts/export.csv")
def export_contacts(campaign_id: str | None = None, db: Session = Depends(get_db)) -> PlainTextResponse:
    campaign = ensure_campaign(db, campaign_id)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["campaign", "name", "phone", "status", "attempts", "last_digit", "next_call_at", "notes"])
    for contact in db.scalars(select(Contact).where(Contact.campaign_id == campaign.id).order_by(Contact.created_at)):
        writer.writerow([campaign.name, contact.name, contact.phone, contact.status, contact.attempts, contact.last_digit or "", format_dt(contact.next_call_at), contact.notes or ""])
    return PlainTextResponse(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="out-caller-contacts.csv"'})


@app.get("/logs")
def list_logs(
    campaign_id: str | None = None,
    limit: int = 100,
    log_order: str = "newest",
    log_status: str = "",
    log_filter: str = "",
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    campaign = ensure_campaign(db, campaign_id)
    return recent_logs(db, campaign.id, min(max(limit, 1), 500), log_order, log_status, log_filter)


@app.get("/logs/export.csv")
def export_logs(
    campaign_id: str | None = None,
    log_order: str = "newest",
    log_status: str = "",
    log_filter: str = "",
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    campaign = ensure_campaign(db, campaign_id)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["campaign", "created_at", "completed_at", "name", "phone", "dial_input", "dial_normalization", "dialed_number", "caller_id_name", "caller_id_number", "sip_to", "sip_from", "sip_route", "sip_target", "sip_last_response", "sip_last_response_at", "status", "digit", "amd_status", "amd_cause", "transcript", "ai_decision", "ai_trace", "voice_recording", "message", "attempt_id", "contact_id"])
    for item in recent_logs(db, campaign.id, 500, log_order, log_status, log_filter):
        writer.writerow([campaign.name, format_dt(item["created_at"]), format_dt(item["completed_at"]), item["name"], item["phone"], item["dial_input"] or "", item["dial_normalization"] or "", item["dialed_number"] or "", item["caller_id_name"] or "", item["caller_id_number"] or "", item["sip_to"] or "", item["sip_from"] or "", item["sip_route"] or "", item["sip_target"] or "", item["sip_last_response"] or "", format_dt(item["sip_last_response_at"]), item["status"], item["digit"] or "", item["amd_status"] or "", item["amd_cause"] or "", item["transcript"] or "", item["ai_decision"] or "", item["ai_trace"] or "", item["voice_recording"] or "", item["message"] or "", item["id"], item["contact_id"]])
    return PlainTextResponse(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="out-caller-call-log.csv"'})


@app.get("/events")
def list_events(
    campaign_id: str | None = None,
    limit: int = 100,
    event_order: str = "newest",
    event_filter: str = "",
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    campaign = ensure_campaign(db, campaign_id)
    return [{"id": e.id, "campaign_id": e.campaign_id, "level": e.level, "source": e.source, "event_type": e.event_type, "message": e.message, "details": e.details, "created_at": e.created_at} for e in recent_events(db, campaign.id, min(max(limit, 1), 500), event_order, event_filter)]


@app.get("/events/export.csv")
def export_events(
    campaign_id: str | None = None,
    event_order: str = "newest",
    event_filter: str = "",
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    campaign = ensure_campaign(db, campaign_id)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["campaign", "created_at", "level", "source", "event_type", "message", "details", "event_id"])
    for event in recent_events(db, campaign.id, 500, event_order, event_filter):
        writer.writerow([campaign.name, format_dt(event.created_at), event.level, event.source, event.event_type, event.message, event.details or "", event.id])
    return PlainTextResponse(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="out-caller-events.csv"'})


@app.get("/sip-trace")
def sip_trace(campaign_id: str | None = None, trace_limit: str = "100", trace_order: str = "newest", trace_filter: str = "", db: Session = Depends(get_db)) -> dict[str, object]:
    ensure_campaign(db, campaign_id)
    return {"log_path": str(Path(ASTERISK_LOG_DIR) / "messages"), "order": trace_order, "limit": trace_limit, "filter": trace_filter, "lines": sip_trace_lines(db, trace_limit if trace_limit in TRACE_LIMITS else "100", trace_order, trace_filter)}


@app.post("/sip-trace/clear")
def clear_sip_trace(request: Request, campaign_id: str = Form(DEFAULT_CAMPAIGN_ID), db: Session = Depends(get_db)):
    add_event(db, "sip_trace_cleared", "SIP trace view was cleared from the web UI.", campaign_id=campaign_id)
    return see_other(request, "SIP trace view cleared", campaign_id=campaign_id, tab="sip")


@app.get("/sip-trace/export.txt")
def export_sip_trace(campaign_id: str | None = None, trace_limit: str = "all", trace_order: str = "newest", trace_filter: str = "", db: Session = Depends(get_db)) -> PlainTextResponse:
    ensure_campaign(db, campaign_id)
    return PlainTextResponse("\n".join(sip_trace_lines(db, trace_limit if trace_limit in TRACE_LIMITS else "all", trace_order, trace_filter)) + "\n", media_type="text/plain", headers={"Content-Disposition": 'attachment; filename="out-caller-sip-trace.txt"'})


@app.get("/recordings/{filename}")
def get_recording(filename: str):
    safe = filename.replace("/", "").replace("\\", "")
    return FileResponse(f"{RECORDINGS_DIR}/{safe}", media_type="audio/wav", filename=safe)


def map_digit(digit: str | None) -> str:
    return {"1": "attending", "2": "not_attending", "3": "unsure", "9": "callback_requested"}.get(digit or "", "no_response")


@app.post("/agi/result")
def agi_result(payload: dict[str, str | None], db: Session = Depends(get_db)) -> dict[str, str]:
    contact_id = payload.get("contact_id")
    attempt_id = payload.get("attempt_id")
    digit = payload.get("digit")
    message = payload.get("message")
    amd_status = payload.get("amd_status")
    amd_cause = payload.get("amd_cause")
    voice_recording = payload.get("voice_recording")
    transcript = payload.get("transcript")
    ai_decision = payload.get("ai_decision")
    ai_trace = payload.get("ai_trace")
    status_override = payload.get("status")
    if not contact_id:
        raise HTTPException(status_code=400, detail="contact_id is required")
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="contact not found")
    status_value = (
        str(status_override)
        if status_override in STATUSES
        else "voicemail_left"
        if amd_status == "MACHINE"
        else "voice_response"
        if voice_recording and not digit
        else map_digit(digit)
    )
    contact.status = status_value
    contact.last_digit = digit
    contact.updated_at = now_utc()
    if attempt_id:
        attempt = db.get(CallAttempt, attempt_id)
        if attempt:
            attempt.status = status_value
            attempt.digit = digit
            attempt.message = message
            attempt.amd_status = amd_status
            attempt.amd_cause = amd_cause
            attempt.voice_recording = voice_recording
            attempt.transcript = transcript
            if ai_decision:
                attempt.ai_decision = ai_decision
            if ai_trace:
                attempt.ai_trace = ai_trace
            attempt.completed_at = now_utc()
    db.commit()
    return {"status": status_value}
