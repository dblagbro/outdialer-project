import os
import re
import socket
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from .config import get_settings
from .db import SessionLocal
from .models import CallAttempt, Campaign, Contact, EventLog, Setting

settings = get_settings()
EVENT_THROTTLE_SECONDS = 300
VALID_DIAL_NORMALIZATIONS = {"nanp_1", "strip_only", "as_entered"}
FINAL_SIP_FAILURE_MIN = 300
SIP_RESPONSE_RE = re.compile(r"SIP/2\.0\s+(\d{3}[^\r\n]*)")
LOG_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
CALL_FILE_RESULT_RE = re.compile(r"^(Status|Set):\s*(.*)$")


def log_event(db, event_type: str, message: str, level: str = "info", details: str | None = None, campaign_id: str | None = None) -> None:
    db.add(EventLog(campaign_id=campaign_id, level=level, source="worker", event_type=event_type, message=message, details=details))
    db.commit()


def should_log_event(db, event_type: str, now: datetime) -> bool:
    key = f"last_event_{event_type}"
    setting = db.get(Setting, key)
    if setting and setting.value:
        try:
            previous = datetime.fromisoformat(setting.value)
            if (now - previous).total_seconds() < EVENT_THROTTLE_SECONDS:
                return False
        except ValueError:
            pass
    if setting:
        setting.value = now.isoformat()
    else:
        db.add(Setting(key=key, value=now.isoformat()))
    db.commit()
    return True


def in_call_window(now: datetime) -> bool:
    local_now = now.astimezone(ZoneInfo(settings.timezone))
    start_h, start_m = [int(part) for part in settings.call_window_start.split(":")]
    end_h, end_m = [int(part) for part in settings.call_window_end.split(":")]
    start = local_now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = local_now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    return start <= local_now <= end


def normalize_phone(phone: str, mode: str) -> str:
    cleaned = re.sub(r"[^0-9+*#]", "", phone or "")
    digits = re.sub(r"[^0-9]", "", cleaned)
    if mode not in VALID_DIAL_NORMALIZATIONS:
        mode = "nanp_1"
    if mode == "as_entered":
        return cleaned
    if mode == "strip_only":
        return re.sub(r"[^0-9*#]", "", cleaned)
    if cleaned.startswith("+1") and len(digits) == 11:
        return digits
    if cleaned.startswith("+") and len(digits) > 0:
        return digits
    if len(digits) == 10:
        return f"1{digits}"
    return cleaned


def clean_dial_prefix(prefix: str) -> str:
    return re.sub(r"[^0-9*#]", "", prefix or "")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def safe_var(value: str | None) -> str:
    return (value or "").replace("\n", " ").replace("\r", " ").replace(";", ",")


def setting_value(db, key: str, default: str) -> str:
    setting = db.get(Setting, key)
    return setting.value if setting and setting.value else default


def setting_int(db, key: str, default: int) -> int:
    value = setting_value(db, key, str(default))
    try:
        return int(value)
    except ValueError:
        return default


def parse_asterisk_time(line: str) -> datetime | None:
    match = LOG_TS_RE.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def sip_log_blocks() -> list[tuple[datetime, str]]:
    path = Path(settings.asterisk_log_dir) / "messages"
    if not path.exists():
        return []
    blocks: list[tuple[datetime, str]] = []
    current_time: datetime | None = None
    current_lines: list[str] = []
    for line in path.read_text(errors="ignore").splitlines():
        line_time = parse_asterisk_time(line)
        if line_time:
            if current_time and current_lines:
                blocks.append((current_time, "\n".join(current_lines)))
            current_time = line_time
            current_lines = [line]
        elif current_time:
            current_lines.append(line)
    if current_time and current_lines:
        blocks.append((current_time, "\n".join(current_lines)))
    return blocks[-2000:]


def latest_sip_response_for_attempt(attempt: CallAttempt, blocks: list[tuple[datetime, str]]) -> tuple[str, datetime] | None:
    if not attempt.dialed_number or not attempt.created_at:
        return None
    created_at = attempt.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    marker = f"<sip:{attempt.dialed_number}@"
    since = created_at - timedelta(seconds=30)
    until = created_at + timedelta(minutes=2)
    best_provisional: tuple[str, datetime] | None = None
    best_failure: tuple[str, datetime] | None = None
    for block_time, block in blocks:
        if block_time < since or block_time > until or marker not in block:
            continue
        for response in SIP_RESPONSE_RE.findall(block):
            try:
                code = int(response[:3])
            except ValueError:
                continue
            item = (response.strip(), block_time)
            if code >= FINAL_SIP_FAILURE_MIN:
                best_failure = item
            elif code >= 100:
                best_provisional = item
    return best_failure or best_provisional


def sync_sip_failures(db, now: datetime) -> None:
    blocks = sip_log_blocks()
    if not blocks:
        return
    attempts = db.scalars(
        select(CallAttempt)
        .where(CallAttempt.created_at >= now - timedelta(hours=24))
        .order_by(CallAttempt.created_at.desc())
        .limit(200)
    ).all()
    for attempt in attempts:
        response = latest_sip_response_for_attempt(attempt, blocks)
        if not response:
            continue
        response_text, response_at = response
        if attempt.sip_last_response == response_text:
            continue
        attempt.sip_last_response = response_text
        attempt.sip_last_response_at = response_at
        if response_text.startswith(("3", "4", "5", "6")) and attempt.status in ["queued", "originated", "failed"]:
            attempt.status = "failed"
            attempt.completed_at = response_at
            attempt.message = f"SIP failure: {response_text}"
            log_event(
                db,
                "call_failed_sip",
                f"Call attempt failed with SIP {response_text}.",
                level="error",
                details=f"attempt_id={attempt.id} contact_id={attempt.contact_id} dialed={attempt.dialed_number}",
                campaign_id=attempt.campaign_id,
            )
    db.commit()


def parse_done_call_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(errors="ignore").splitlines():
        match = CALL_FILE_RESULT_RE.match(line.strip())
        if not match:
            continue
        key, value = match.groups()
        if key == "Status":
            result["status"] = value.strip()
        elif value.startswith("ATTEMPT_ID="):
            result["attempt_id"] = value.split("=", 1)[1].strip()
    if "attempt_id" not in result and path.name.endswith(".call"):
        result["attempt_id"] = path.name[:-5]
    return result


def sync_call_file_results(db, now: datetime) -> None:
    done_dir = Path(settings.asterisk_outgoing_dir).parent / "outgoing_done"
    if not done_dir.exists():
        return
    cutoff = now - timedelta(hours=24)
    for path in sorted(done_dir.glob("*.call"), key=lambda item: item.stat().st_mtime, reverse=True)[:500]:
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            break
        result = parse_done_call_file(path)
        asterisk_status = result.get("status", "")
        if asterisk_status not in {"Expired", "Failed"}:
            continue
        attempt_id = result.get("attempt_id")
        if not attempt_id:
            continue
        attempt = db.get(CallAttempt, attempt_id)
        if not attempt or attempt.status not in {"queued", "originated"} or attempt.completed_at:
            continue
        attempt.status = "failed"
        attempt.completed_at = mtime
        reason = (
            "Asterisk call file expired before the call connected"
            if asterisk_status == "Expired"
            else "Asterisk call file failed before completion"
        )
        attempt.message = f"{attempt.message or 'Call file handed to Asterisk'}; {reason}"
        log_event(
            db,
            "call_failed_call_file",
            f"Call attempt {asterisk_status.lower()} in Asterisk outgoing_done.",
            level="error",
            details=f"attempt_id={attempt.id} contact_id={attempt.contact_id} dialed={attempt.dialed_number} file={path.name}",
            campaign_id=attempt.campaign_id,
        )
    db.commit()


def call_metadata(campaign: Campaign, contact: Contact) -> dict[str, str]:
    normalization = campaign.dial_normalization or "nanp_1"
    if normalization not in VALID_DIAL_NORMALIZATIONS:
        normalization = "nanp_1"
    normalized_phone = normalize_phone(contact.phone, normalization)
    dial_prefix = clean_dial_prefix(campaign.outbound_dial_prefix)
    phone = f"{dial_prefix}{normalized_phone}"
    caller_id_name = campaign.caller_id_name or settings.caller_id_name
    caller_id_number = campaign.caller_id_number or settings.caller_id_number
    from_domain = os.getenv("AVAYA_FROM_DOMAIN", "")
    avaya_host = os.getenv("AVAYA_SIP_HOST", "")
    request_uri_host = os.getenv("AVAYA_SIP_CONTACT_HOST", "") or avaya_host
    outbound_proxy = os.getenv("AVAYA_OUTBOUND_PROXY", "")
    user_phone_param = ";user=phone" if env_bool("AVAYA_SIP_USER_PHONE", True) else ""
    sip_to = f"sip:{phone}@{request_uri_host}{user_phone_param}"
    sip_route_uri = f"sip:{phone}@{request_uri_host}:5060{user_phone_param}"
    sip_channel = f"PJSIP/avaya/{sip_route_uri}"
    identity_number = caller_id_number or os.getenv("AVAYA_SIP_USERNAME", "")
    sip_from_domain = from_domain or avaya_host
    sip_from = f"sip:{identity_number}@{sip_from_domain}"
    return {
        "phone": phone,
        "dial_input": contact.phone,
        "dial_prefix": dial_prefix,
        "dial_normalization": normalization,
        "caller_id_name": caller_id_name,
        "caller_id_number": caller_id_number,
        "sip_identity_number": identity_number,
        "sip_from_domain": sip_from_domain,
        "sip_channel": sip_channel,
        "sip_to": sip_to,
        "sip_from": sip_from,
        "sip_route": outbound_proxy or sip_route_uri,
        "sip_target": request_uri_host,
    }


def sip_target_reachable(meta: dict[str, str]) -> tuple[bool, str]:
    transport = os.getenv("AVAYA_SIP_TRANSPORT", "udp").strip().lower()
    host = meta.get("sip_target") or os.getenv("AVAYA_SIP_HOST", "")
    try:
        port = int(os.getenv("AVAYA_SIP_PORT", "5060"))
    except ValueError:
        port = 5060
    if not host:
        return False, "SIP target host is blank"
    if transport != "tcp":
        return True, f"reachability preflight skipped for {transport or 'udp'} transport"
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True, f"tcp {host}:{port} reachable"
    except OSError as exc:
        return False, f"tcp {host}:{port} unreachable: {exc}"


def write_call_file(contact: Contact, attempt: CallAttempt, meta: dict[str, str]) -> None:
    outgoing = Path(settings.asterisk_outgoing_dir)
    tmp_dir = outgoing.parent / "tmp"
    outgoing.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    outgoing.chmod(0o777)
    tmp_dir.chmod(0o777)
    tmp_path = tmp_dir / f"{attempt.id}.call.tmp"
    final_path = outgoing / f"{attempt.id}.call"
    lines = [
            f"Channel: {meta['sip_channel']}",
            f"CallerID: \"{meta['caller_id_name']}\" <{meta['caller_id_number']}>",
            "MaxRetries: 0",
            "RetryTime: 60",
            "WaitTime: 35",
            "Context: birthday-rsvp",
            "Extension: s",
            "Priority: 1",
            f"Set: CAMPAIGN_ID={safe_var(contact.campaign_id or '')}",
            f"Set: CONTACT_ID={contact.id}",
            f"Set: ATTEMPT_ID={attempt.id}",
            f"Set: CONTACT_NAME={safe_var(contact.name)}",
            f"Set: OUTDIALER_DIALED_NUMBER={safe_var(meta['phone'])}",
            f"Set: OUTDIALER_CALLER_ID_NAME={safe_var(meta['caller_id_name'])}",
            f"Set: OUTDIALER_CALLER_ID_NUMBER={safe_var(meta['caller_id_number'])}",
            f"Set: CALLERID(name)={safe_var(meta['caller_id_name'])}",
            f"Set: CALLERID(num)={safe_var(meta['sip_identity_number'])}",
        ]
    if meta.get("sip_identity_number") and meta.get("sip_from_domain"):
        lines.extend(
            [
                f"Set: PJSIP_HEADER(add,P-Asserted-Identity)=<sip:{safe_var(meta['sip_identity_number'])}@{safe_var(meta['sip_from_domain'])}>",
                f"Set: PJSIP_HEADER(add,P-Preferred-Identity)=<sip:{safe_var(meta['sip_identity_number'])}@{safe_var(meta['sip_from_domain'])}>",
            ]
        )
    lines.extend(["Archive: yes", ""])
    content = "\n".join(lines)
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.chmod(0o660)
    uid = int(os.getenv("ASTERISK_SPOOL_UID", "101"))
    gid = int(os.getenv("ASTERISK_SPOOL_GID", "101"))
    os.chown(tmp_path, uid, gid)
    os.replace(tmp_path, final_path)


def tick() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        sync_call_file_results(db, now)
        sync_sip_failures(db, now)
        campaigns = db.scalars(select(Campaign).where(Campaign.enabled == 1).order_by(Campaign.created_at)).all()
        if not campaigns:
            if should_log_event(db, "campaign_stopped", now):
                log_event(db, "campaign_stopped", "Worker skipped dialing because no campaign is running.")
            return

        for campaign in campaigns:
            timezone_name = campaign.timezone or settings.timezone
            window_start = campaign.call_window_start or settings.call_window_start
            window_end = campaign.call_window_end or settings.call_window_end
            retry_minutes = campaign.retry_minutes or settings.retry_minutes
            max_attempts = campaign.max_attempts or settings.max_attempts
            max_calls = campaign.max_calls_per_worker_tick or settings.max_calls_per_worker_tick
            local_now = now.astimezone(ZoneInfo(timezone_name))
            start_h, start_m = [int(part) for part in window_start.split(":")]
            end_h, end_m = [int(part) for part in window_end.split(":")]
            start = local_now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            end = local_now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

            if not (start <= local_now <= end):
                if should_log_event(db, f"call_window_closed_{campaign.id}", now):
                    log_event(
                        db,
                        "call_window_closed",
                        f"Worker skipped {campaign.name} because the current time is outside the configured call window.",
                        details=f"utc={now.isoformat()} local={local_now.isoformat()} timezone={timezone_name} window={window_start}-{window_end}",
                        campaign_id=campaign.id,
                    )
                continue

            contacts = db.scalars(
                select(Contact)
                .where(Contact.campaign_id == campaign.id)
                .where(Contact.status.in_(["pending", "no_response"]))
                .where(Contact.attempts < max_attempts)
                .where(or_(Contact.next_call_at.is_(None), Contact.next_call_at <= now))
                .order_by(Contact.created_at)
                .limit(max_calls)
            ).all()

            if not contacts:
                waiting_contact = db.scalars(
                    select(Contact)
                    .where(Contact.campaign_id == campaign.id)
                    .where(Contact.status.in_(["pending", "no_response"]))
                    .where(Contact.attempts < max_attempts)
                    .where(Contact.next_call_at.is_not(None))
                    .where(Contact.next_call_at > now)
                    .order_by(Contact.next_call_at)
                    .limit(1)
                ).first()
                if waiting_contact:
                    if should_log_event(db, f"contacts_waiting_retry_{campaign.id}", now):
                        log_event(
                            db,
                            "contacts_waiting_retry",
                            f"No contacts are due right now in {campaign.name}; next retry is {waiting_contact.name}.",
                            details=(
                                f"contact_id={waiting_contact.id} next_call_at={waiting_contact.next_call_at.isoformat()} "
                                f"utc_now={now.isoformat()} max_calls_per_tick={max_calls}"
                            ),
                            campaign_id=campaign.id,
                        )
                    continue
                if should_log_event(db, f"no_eligible_contacts_{campaign.id}", now):
                    log_event(db, "no_eligible_contacts", f"Worker found no pending contacts eligible for dialing in {campaign.name}.", campaign_id=campaign.id)
                continue

            for contact in contacts:
                if contact.next_call_at and contact.next_call_at > now:
                    if should_log_event(db, f"contact_waiting_retry_{campaign.id}_{contact.id}", now):
                        log_event(
                            db,
                            "contact_waiting_retry",
                            f"Skipped {contact.name}; next retry time has not arrived.",
                            details=f"contact_id={contact.id} next_call_at={contact.next_call_at.isoformat()} utc_now={now.isoformat()}",
                            campaign_id=campaign.id,
                        )
                    continue
                meta = call_metadata(campaign, contact)
                reachable, reachability_detail = sip_target_reachable(meta)
                if not reachable:
                    if should_log_event(db, f"sip_target_unreachable_{campaign.id}_{meta['sip_target']}", now):
                        log_event(
                            db,
                            "sip_target_unreachable",
                            f"Skipped {contact.name}; SIP target is unreachable, so no attempt was counted.",
                            level="error",
                            details=(
                                f"contact_id={contact.id} input={meta['dial_input']} dialed={meta['phone']} "
                                f"sip_target={meta['sip_target']} sip_route={meta['sip_route']} detail={reachability_detail}"
                            ),
                            campaign_id=campaign.id,
                        )
                    continue
                log_event(
                    db,
                    "queue_call",
                    f"Queueing outbound call to {contact.name}.",
                    details=(
                        f"contact_id={contact.id} input={meta['dial_input']} normalization={meta['dial_normalization']} "
                        f"prefix={meta['dial_prefix']} dialed={meta['phone']} caller_id={meta['caller_id_name']} <{meta['caller_id_number']}> "
                        f"sip_to={meta['sip_to']} sip_from={meta['sip_from']} sip_route={meta['sip_route']} sip_target={meta['sip_target']}"
                    ),
                    campaign_id=campaign.id,
                )
                attempt = CallAttempt(
                    campaign_id=campaign.id,
                    contact_id=contact.id,
                    status="queued",
                    dial_input=meta["dial_input"],
                    dial_normalization=meta["dial_normalization"],
                    dialed_number=meta["phone"],
                    caller_id_name=meta["caller_id_name"],
                    caller_id_number=meta["caller_id_number"],
                    sip_to=meta["sip_to"],
                    sip_from=meta["sip_from"],
                    sip_route=meta["sip_route"],
                    sip_target=meta["sip_target"],
                )
                db.add(attempt)
                contact.attempts += 1
                contact.next_call_at = now + timedelta(minutes=retry_minutes)
                db.commit()
                write_call_file(contact, attempt, meta)
                attempt.status = "originated"
                attempt.message = f"Call file handed to Asterisk for {meta['phone']} via {meta['sip_route']}"
                db.commit()
                log_event(
                    db,
                    "call_file_written",
                    f"Call file written for {contact.name}.",
                    details=f"attempt_id={attempt.id} input={meta['dial_input']} normalization={meta['dial_normalization']} channel={meta['sip_channel']}",
                    campaign_id=campaign.id,
                )


def run_worker() -> None:
    while True:
        try:
            tick()
        except Exception as exc:
            with SessionLocal() as db:
                log_event(db, "worker_error", f"Worker tick failed: {exc}", level="error", details=traceback.format_exc())
        time.sleep(settings.worker_tick_seconds)
