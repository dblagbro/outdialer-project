#!/usr/bin/env python3
import json
import hashlib
import os
import re
import shlex
import subprocess
import sys
import urllib.request
import wave
from pathlib import Path

import requests


DEFAULT_SCRIPTS = {
    "intro_script": "Hi {contact_name}. This is Devin's Out Caller. Press 1 if you are attending, then stay on the line for one quick headcount question. Press 2 if you cannot attend. Press 3 if you are not sure. Press 9 if you would like a person to call you back. Or, after the tone, say yes, no, not sure, or call me back.",
    "voicemail_script": "Hello. This is Devin's Out Caller. Please call us back. Goodbye.",
    "voice_prompt_script": "Please say yes, no, not sure, or call me back after the tone.",
    "attending_followup_script": "Great. For the caterer, please enter the total number of people coming, including yourself, using one or two digits. Or, after the tone, say the total and whether you are bringing kids, friends, or other family.",
    "thanks_attending_script": "Thank you. We have you marked as attending with a total headcount of {party_size}. Goodbye.",
    "headcount_missing_script": "Thank you. We have you marked as attending, but we did not catch the headcount. Someone may follow up. Goodbye.",
    "thanks_not_attending_script": "Thank you. We have you marked as not attending. Goodbye.",
    "thanks_unsure_script": "Thank you. We have you marked as unsure. Goodbye.",
    "thanks_callback_script": "Thank you. Someone will call you back. Goodbye.",
    "no_response_script": "Sorry, we did not get a response. We may try again another time. Goodbye.",
}


class SafeVars(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def agi_read_env() -> dict[str, str]:
    env = {}
    while True:
        line = sys.stdin.readline().strip()
        if not line:
            break
        key, _, value = line.partition(":")
        env[key.strip()] = value.strip()
    return env


def agi(command: str) -> str:
    sys.stdout.write(command + "\n")
    sys.stdout.flush()
    return sys.stdin.readline().strip()


def quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def get_variable(name: str, default: str = "") -> str:
    response = agi(f"GET VARIABLE {name}")
    marker = "result=1 ("
    if marker not in response:
        return default
    return response.split(marker, 1)[1].rsplit(")", 1)[0]


def deepgram_api_key() -> str:
    return os.getenv("DEEPGRAM_API_KEY", "").strip()


def env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def prompt_cache_salt() -> str:
    if deepgram_api_key() and env_bool("DEEPGRAM_TTS_ENABLED", True):
        model = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-apollo-en").strip()
        sample_rate = os.getenv("DEEPGRAM_TTS_SAMPLE_RATE", "8000").strip()
        return f"deepgram:{model}:{sample_rate}"
    bridge_url = os.getenv("WHISPER_BRIDGE_URL", "").strip()
    if bridge_url:
        return f"bridge:{bridge_url}"
    return "espeak-ng:en-us:145"


def make_prompt(prompt_id: str, text: str) -> str:
    digest = hashlib.sha1(f"{prompt_cache_salt()}\n{text}".encode("utf-8")).hexdigest()[:12]
    prompt_name = f"{prompt_id}-{digest}"
    out_base = f"/var/lib/asterisk/sounds/generated/{prompt_name}"
    out_wav = f"{out_base}.wav"
    raw_wav = f"{out_base}.raw.wav"
    if not is_asterisk_wav(out_wav):
        if not synthesize_prompt(raw_wav, text):
            subprocess.run(
                ["espeak-ng", "-s", "145", "-v", "en-us", "-w", raw_wav, text],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(
            ["sox", raw_wav, "-r", "8000", "-c", "1", "-b", "16", out_wav],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            os.remove(raw_wav)
        except FileNotFoundError:
            pass
    return out_base


def auth_headers() -> dict[str, str]:
    token = os.getenv("WHISPER_BRIDGE_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def synthesize_prompt(out_wav: str, text: str) -> bool:
    if synthesize_with_deepgram(out_wav, text):
        return True
    base_url = os.getenv("WHISPER_BRIDGE_URL", "").strip().rstrip("/")
    if not base_url:
        return False
    try:
        response = requests.post(
            f"{base_url}/speak",
            json={"text": text},
            headers=auth_headers(),
            timeout=env_float("TTS_TIMEOUT_SECONDS", 2.0),
        )
        response.raise_for_status()
        with open(out_wav, "wb") as handle:
            handle.write(response.content)
        return True
    except Exception:
        return False


def synthesize_with_deepgram(out_wav: str, text: str) -> bool:
    api_key = deepgram_api_key()
    if not api_key or not env_bool("DEEPGRAM_TTS_ENABLED", True):
        return False
    model = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-apollo-en").strip() or "aura-2-apollo-en"
    sample_rate = os.getenv("DEEPGRAM_TTS_SAMPLE_RATE", "8000").strip() or "8000"
    url = os.getenv("DEEPGRAM_TTS_URL", "https://api.deepgram.com/v1/speak").strip()
    try:
        response = requests.post(
            url,
            params={
                "model": model,
                "encoding": "linear16",
                "container": "wav",
                "sample_rate": sample_rate,
            },
            json={"text": text},
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": "application/json",
            },
            timeout=env_float("DEEPGRAM_TTS_TIMEOUT_SECONDS", env_float("TTS_TIMEOUT_SECONDS", 8.0)),
        )
        response.raise_for_status()
        with open(out_wav, "wb") as handle:
            handle.write(response.content)
        return True
    except Exception as exc:
        sys.stderr.write(f"Deepgram TTS failed; falling back: {exc}\n")
        sys.stderr.flush()
        return False


def is_asterisk_wav(path: str) -> bool:
    try:
        with wave.open(path, "rb") as wav_file:
            return (
                wav_file.getnchannels() == 1
                and wav_file.getframerate() == 8000
                and wav_file.getsampwidth() == 2
            )
    except (FileNotFoundError, wave.Error, EOFError):
        return False


def fetch_campaign_config(campaign_id: str) -> dict[str, str]:
    base_url = os.getenv("PUBLIC_BASE_URL", "http://outdialer-api:8080").rstrip("/")
    config = DEFAULT_SCRIPTS.copy()
    config.update(
        {
            "_ai_enabled": "1",
            "_ai_provider": "local",
            "_ai_observe_ms": os.getenv("AI_GREETING_RECORD_MS", "0"),
            "_ai_listen_ms": "7000",
            "_ai_max_turns": "3",
        }
    )
    if not campaign_id:
        return config
    try:
        with urllib.request.urlopen(f"{base_url}/agi/campaign/{campaign_id}/script", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return config
    config.update({key: str(value) for key, value in data.items() if value is not None})
    for key, value in DEFAULT_SCRIPTS.items():
        if not config.get(key):
            config[key] = value
    return config


def render_script(template: str, variables: dict[str, str]) -> str:
    return template.format_map(SafeVars(variables))


def post_result(
    contact_id: str,
    attempt_id: str,
    digit: str,
    message: str,
    amd_status: str = "",
    amd_cause: str = "",
    voice_recording: str = "",
    transcript: str = "",
    status: str = "",
    ai_decision: str = "",
    ai_trace: str = "",
    party_size: str = "",
    party_kids: str = "",
    party_friends: str = "",
    party_family: str = "",
    party_details: str = "",
) -> None:
    base_url = os.getenv("PUBLIC_BASE_URL", "http://outdialer-api:8080").rstrip("/")
    payload = json.dumps(
        {
            "contact_id": contact_id,
            "attempt_id": attempt_id,
            "digit": digit,
            "message": message,
            "amd_status": amd_status,
            "amd_cause": amd_cause,
            "voice_recording": voice_recording,
            "transcript": transcript,
            "status": status,
            "ai_decision": ai_decision,
            "ai_trace": ai_trace,
            "party_size": party_size,
            "party_kids": party_kids,
            "party_friends": party_friends,
            "party_family": party_family,
            "party_details": party_details,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/agi/result",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(request, timeout=5).read()


def post_decision(payload: dict[str, object]) -> dict[str, object]:
    base_url = os.getenv("PUBLIC_BASE_URL", "http://outdialer-api:8080").rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/agi/decision",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=35) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, dict) else {"action": "legacy", "reason": "decision response was not an object"}


def classify_transcript(transcript: str) -> str:
    text = transcript.lower()
    if re.search(r"\b(yes|attending|coming|will be there|we'll be there)\b", text):
        return "1"
    if re.search(r"\b(no|not attending|can't|cannot|won't|unable)\b", text):
        return "2"
    if re.search(r"\b(maybe|not sure|unsure|don't know)\b", text):
        return "3"
    if re.search(r"\b(call me|callback|call back)\b", text):
        return "9"
    return ""


def maybe_transcribe(recording_path: str) -> str:
    deepgram_transcript = transcribe_with_deepgram(recording_path)
    if deepgram_transcript:
        return deepgram_transcript
    bridge_transcript = transcribe_with_bridge(recording_path)
    if bridge_transcript:
        return bridge_transcript
    transcriber = os.getenv("VOICE_TRANSCRIBE_COMMAND", "").strip()
    if not transcriber or not recording_path:
        return ""
    try:
        completed = subprocess.run(
            shlex.split(transcriber) + [recording_path],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=20,
        )
    except Exception:
        return ""
    return completed.stdout.strip()[:1000]


def transcribe_with_deepgram(recording_path: str) -> str:
    api_key = deepgram_api_key()
    if not api_key or not env_bool("DEEPGRAM_STT_ENABLED", True) or not recording_path or not os.path.exists(recording_path):
        return ""
    params = {
        "model": os.getenv("DEEPGRAM_STT_MODEL", "nova-3").strip() or "nova-3",
        "smart_format": "true",
        "punctuate": "true",
    }
    language = os.getenv("DEEPGRAM_STT_LANGUAGE", "en-US").strip()
    if language:
        params["language"] = language
    try:
        with open(recording_path, "rb") as handle:
            response = requests.post(
                os.getenv("DEEPGRAM_STT_URL", "https://api.deepgram.com/v1/listen").strip(),
                params=params,
                data=handle.read(),
                headers={
                    "Authorization": f"Token {api_key}",
                    "Content-Type": "audio/wav",
                },
                timeout=env_float("DEEPGRAM_STT_TIMEOUT_SECONDS", 12.0),
            )
        response.raise_for_status()
        data = response.json()
        channels = data.get("results", {}).get("channels", [])
        if not channels:
            return ""
        alternatives = channels[0].get("alternatives", [])
        if not alternatives:
            return ""
        return str(alternatives[0].get("transcript") or "").strip()[:1000]
    except Exception as exc:
        sys.stderr.write(f"Deepgram STT failed; falling back: {exc}\n")
        sys.stderr.flush()
        return ""


def transcribe_with_bridge(recording_path: str) -> str:
    base_url = os.getenv("WHISPER_BRIDGE_URL", "").strip().rstrip("/")
    if not base_url or not recording_path or not os.path.exists(recording_path):
        return ""
    try:
        with open(recording_path, "rb") as handle:
            response = requests.post(
                f"{base_url}/transcribe",
                files={"file": (os.path.basename(recording_path), handle, "audio/wav")},
                headers=auth_headers(),
                timeout=35,
            )
        response.raise_for_status()
        data = response.json()
        return str(data.get("text") or "").strip()[:1000]
    except Exception:
        return ""


def record_clip_with_response(
    recording_dir: Path,
    attempt_id: str,
    label: str,
    timeout_ms: int,
    silence_seconds: int = 1,
    escape_digits: str = "#",
) -> tuple[str, str, str]:
    safe_attempt_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (attempt_id or "inbound"))
    recording_base = recording_dir / f"{safe_attempt_id}-{label}"
    response = agi(f"RECORD FILE {quote(str(recording_base))} wav {quote(escape_digits)} {timeout_ms} 0 s={silence_seconds}")
    recording_path = f"{recording_base}.wav"
    return recording_path, os.path.basename(recording_path) if os.path.exists(recording_path) else "", response


def record_clip(recording_dir: Path, attempt_id: str, label: str, timeout_ms: int, silence_seconds: int = 1) -> tuple[str, str]:
    recording_path, recording_file, _ = record_clip_with_response(recording_dir, attempt_id, label, timeout_ms, silence_seconds, "#")
    return recording_path, recording_file


def observe_answer(recording_dir: Path, attempt_id: str, timeout_ms: int) -> tuple[str, str, str]:
    silence_seconds = int(os.getenv("AI_GREETING_SILENCE_SECONDS", "1"))
    recording_path, recording_file = record_clip(recording_dir, attempt_id, "greeting", timeout_ms, silence_seconds)
    transcript = maybe_transcribe(recording_path if recording_file else "")
    return transcript, recording_file, classify_answer(transcript)


def classify_answer(transcript: str) -> str:
    text = transcript.lower().strip()
    if not text:
        return "unknown"
    voicemail_markers = [
        "leave a message",
        "leave your message",
        "at the tone",
        "after the beep",
        "mailbox",
        "voicemail",
        "your call has been forwarded",
        "not available",
        "unavailable",
        "can't take your call",
        "cannot take your call",
        "record your message",
        "automated voice messaging system",
        "automated voice message",
        "voice messaging system",
    ]
    if any(marker in text for marker in voicemail_markers):
        return "machine"
    human_markers = ["hello", "hi", "speaking", "this is", "who is", "who's", "yes"]
    if any(marker in text for marker in human_markers):
        return "human"
    if len(text.split()) > 12:
        return "machine"
    return "human"


def int_config(config: dict[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def enabled(value: str) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def extract_digits(response: str, max_digits: int = 1) -> str:
    if "result=" not in response:
        return ""
    value = response.split("result=", 1)[1].split()[0]
    if value in {"", "0", "-1"} or value.startswith("-"):
        return ""
    digits = re.sub(r"\D", "", value)
    return digits[:max(1, min(max_digits, 2))]


def extract_rsvp_digit(response: str) -> str:
    value = extract_digits(response, 1)
    return value if value in {"1", "2", "3", "9"} else ""


def extract_agi_digit(response: str) -> str:
    if "result=" not in response:
        return ""
    value = response.split("result=", 1)[1].split()[0]
    if value in {"", "0", "-1"} or value.startswith("-"):
        return ""
    if re.fullmatch(r"\d+", value):
        parsed = int(value)
        if 48 <= parsed <= 57 or parsed in {35, 42}:
            return chr(parsed)
        if len(value) == 1:
            return value
    return value[:1]


def valid_stage_digit(stage: str, digit: str) -> bool:
    if not digit or digit == "#":
        return False
    if stage == "attending_followup":
        return digit.isdigit()
    return digit in {"1", "2", "3", "9"}


def stage_escape_digits(stage: str) -> str:
    return "0123456789#" if stage == "attending_followup" else "1239#"


def collect_more_digits(first_digit: str, max_digits: int, stage: str) -> str:
    if not valid_stage_digit(stage, first_digit):
        return ""
    digits = first_digit
    if stage != "attending_followup" or max_digits <= 1:
        return digits
    interdigit_ms = int(os.getenv("DTMF_INTERDIGIT_MS", "1200"))
    while len(digits) < max_digits:
        response = agi(f"WAIT FOR DIGIT {interdigit_ms}")
        if agi_hung_up(response):
            return digits
        next_digit = extract_agi_digit(response)
        if not valid_stage_digit(stage, next_digit):
            break
        digits += next_digit
    return digits


def digit_from_agi_response(response: str, max_digits: int, stage: str) -> str:
    return collect_more_digits(extract_agi_digit(response), max_digits, stage)


def attending_followup_fast_path(scripts: dict[str, str], script_vars: dict[str, str], listen_ms: int) -> dict[str, object]:
    return {
        "action": "speak_and_listen",
        "text": render_script(scripts["attending_followup_script"], script_vars),
        "status": "collecting_headcount",
        "listen_ms": listen_ms,
        "next_stage": "attending_followup",
        "collect_digits": 2,
        "hangup_after": False,
        "reason": "local fast-path after DTMF attending response",
        "source": "agi_fast_path",
    }


def menu_digit_fast_path(digit: str, scripts: dict[str, str], script_vars: dict[str, str], listen_ms: int) -> dict[str, object]:
    if digit == "1":
        return attending_followup_fast_path(scripts, script_vars, listen_ms)
    script_key = {
        "2": "thanks_not_attending_script",
        "3": "thanks_unsure_script",
        "9": "thanks_callback_script",
    }.get(digit, "no_response_script")
    status = {"2": "not_attending", "3": "unsure", "9": "callback_requested"}.get(digit, "no_response")
    return {
        "action": "mark_rsvp",
        "digit": digit,
        "rsvp": status,
        "status": status,
        "text": render_script(scripts[script_key], script_vars),
        "reason": "local fast-path after DTMF RSVP response",
        "source": "agi_fast_path",
    }


def agi_hung_up(response: str) -> bool:
    value = (response or "").strip().upper()
    return not value or value.startswith("HANGUP") or "RESULT=-1" in value


def append_trace(trace: list[dict[str, object]], stage: str, decision: dict[str, object], transcript: str = "", digit: str = "", recording: str = "") -> None:
    trace.append(
        {
            "stage": stage,
            "decision": decision,
            "transcript": transcript[:500],
            "digit": digit,
            "recording": recording,
        }
    )


def decision_payload(
    campaign_id: str,
    contact_id: str,
    attempt_id: str,
    contact_name: str,
    stage: str,
    turn: int,
    transcript: str = "",
    answer_class: str = "",
    digit: str = "",
) -> dict[str, object]:
    return {
        "campaign_id": campaign_id,
        "contact_id": contact_id,
        "attempt_id": attempt_id,
        "contact_name": contact_name,
        "stage": stage,
        "turn": turn,
        "transcript": transcript,
        "answer_class": answer_class,
        "digit": digit,
    }


def decision_text(decision: dict[str, object], fallback: str) -> str:
    text = str(decision.get("text") or "").strip()
    return text or fallback


def decision_party_fields(decision: dict[str, object]) -> dict[str, str]:
    return {
        "party_size": str(decision.get("party_size") or ""),
        "party_kids": str(decision.get("party_kids") if decision.get("party_kids") is not None else ""),
        "party_friends": str(decision.get("party_friends") if decision.get("party_friends") is not None else ""),
        "party_family": str(decision.get("party_family") if decision.get("party_family") is not None else ""),
        "party_details": str(decision.get("party_details") or ""),
    }


def result_recording(full_call_recording: str, clip_recording: str = "") -> str:
    return full_call_recording or clip_recording


def stream_text(prompt_id: str, text: str) -> None:
    if text:
        agi(f"STREAM FILE {quote(make_prompt(prompt_id, text))} \"\"")


def legacy_flow(
    contact_id: str,
    attempt_id: str,
    scripts: dict[str, str],
    script_vars: dict[str, str],
    recording_dir: Path,
    amd_status: str,
    amd_cause: str,
    greeting_transcript: str = "",
    greeting_recording: str = "",
    full_call_recording: str = "",
) -> None:
    if amd_status == "MACHINE":
        agi("EXEC WaitForSilence 900,1,6")
        voicemail = render_script(scripts["voicemail_script"], script_vars)
        voicemail_file = make_prompt("rsvp-voicemail", voicemail)
        agi(f"STREAM FILE {quote(voicemail_file)} \"\"")
        post_result(
            contact_id,
            attempt_id,
            "",
            "voicemail detected" + (f"; greeting={greeting_transcript}" if greeting_transcript else ""),
            amd_status,
            amd_cause,
            result_recording(full_call_recording, greeting_recording),
            greeting_transcript,
            "voicemail_left",
        )
        return

    intro = render_script(scripts["intro_script"], script_vars)
    prompt_file = make_prompt("rsvp-main", intro)
    response = agi(f"GET DATA {shlex.quote(prompt_file)} 10000 1")
    digit = extract_rsvp_digit(response)
    party_size = ""
    party_details = ""
    status = ""

    valid = {"1", "2", "3", "9"}
    if digit not in valid:
        digit = ""
        voice_prompt = make_prompt("rsvp-say-answer", render_script(scripts["voice_prompt_script"], script_vars))
        agi(f"STREAM FILE {quote(voice_prompt)} \"\"")
        recording_path, voice_recording = record_clip(recording_dir, attempt_id, "voice", 7000, 3)
        transcript = maybe_transcribe(recording_path if voice_recording else "")
        digit = classify_transcript(transcript)
        if digit:
            response = f"{response}; voice={voice_recording}; transcript={transcript}"
        else:
            response = f"{response}; voice={voice_recording}"
            thanks = "Sorry, we did not get a response. We may try again another time. Goodbye."
    if digit == "1":
        followup = render_script(scripts["attending_followup_script"], script_vars)
        followup_file = make_prompt("rsvp-headcount", followup)
        headcount_response = agi(f"GET DATA {shlex.quote(followup_file)} 10000 2")
        headcount_digits = extract_digits(headcount_response, 2)
        if headcount_digits:
            party_size = str(int(headcount_digits))
            script_vars["party_size"] = party_size
            status = "attending"
            response = f"{response}; headcount_response={headcount_response}; party_size={party_size}"
            thanks = render_script(scripts["thanks_attending_script"], script_vars)
        else:
            status = "attending_needs_headcount"
            recording_path, headcount_recording = record_clip(recording_dir, attempt_id, "headcount", 7000, 2)
            headcount_transcript = maybe_transcribe(recording_path if headcount_recording else "")
            party_details = headcount_transcript
            response = f"{response}; headcount_response={headcount_response}; headcount_voice={headcount_recording}; headcount_transcript={headcount_transcript}"
            thanks = render_script(scripts["headcount_missing_script"], script_vars)
    elif digit == "2":
        thanks = render_script(scripts["thanks_not_attending_script"], script_vars)
    elif digit == "3":
        thanks = render_script(scripts["thanks_unsure_script"], script_vars)
    elif digit == "9":
        thanks = render_script(scripts["thanks_callback_script"], script_vars)
    else:
        thanks = render_script(scripts["no_response_script"], script_vars)

    post_result(
        contact_id,
        attempt_id,
        digit,
        response + (f"; greeting={greeting_transcript}" if greeting_transcript else ""),
        amd_status,
        amd_cause,
        result_recording(full_call_recording, locals().get("headcount_recording", "") or locals().get("voice_recording", "") or greeting_recording),
        locals().get("headcount_transcript", "") or locals().get("transcript", "") or greeting_transcript,
        status,
        "",
        "",
        party_size,
        "",
        "",
        "",
        party_details,
    )
    thanks_file = make_prompt(f"rsvp-thanks-{digit or 'none'}", thanks)
    agi(f"STREAM FILE {quote(thanks_file)} \"\"")


def run_ai_flow(
    campaign_id: str,
    contact_id: str,
    attempt_id: str,
    contact_name: str,
    scripts: dict[str, str],
    script_vars: dict[str, str],
    recording_dir: Path,
    amd_status: str,
    amd_cause: str,
    answer_class: str,
    greeting_transcript: str,
    greeting_recording: str,
    listen_ms: int,
    max_turns: int,
    full_call_recording: str,
) -> bool:
    trace: list[dict[str, object]] = []
    last_transcript = greeting_transcript
    last_recording = greeting_recording
    last_response_stage = "answer_observed"
    turn = 0
    try:
        decision = post_decision(
            decision_payload(
                campaign_id,
                contact_id,
                attempt_id,
                contact_name,
                "answer_observed",
                turn,
                greeting_transcript,
                answer_class,
            )
        )
    except Exception as exc:
        append_trace(trace, "decision_error", {"action": "legacy", "reason": str(exc)})
        return False
    append_trace(trace, "answer_observed", decision, greeting_transcript, "", greeting_recording)
    if decision.get("action") == "legacy":
        return False

    while turn <= max_turns + 1:
        action = str(decision.get("action") or "")
        decision_blob = json.dumps(decision, ensure_ascii=True, separators=(",", ":"))
        trace_blob = json.dumps(trace[-20:], ensure_ascii=True)
        if action == "leave_voicemail":
            agi("EXEC WaitForSilence 900,1,6")
            stream_text("ai-voicemail", decision_text(decision, render_script(scripts["voicemail_script"], script_vars)))
            post_result(
                contact_id,
                attempt_id,
                "",
                f"AI voicemail: {decision.get('reason', '')}; greeting={greeting_transcript}",
                "MACHINE",
                amd_cause or "AI_OBSERVE:machine",
                result_recording(full_call_recording, greeting_recording),
                greeting_transcript,
                "voicemail_left",
                decision_blob,
                trace_blob,
            )
            return True
        if action in {"mark_rsvp", "complete", "hangup"}:
            digit = str(decision.get("digit") or "")
            status = str(decision.get("status") or "")
            party_fields = decision_party_fields(decision)
            script_vars.update(party_fields)
            if action == "mark_rsvp" and digit not in {"1", "2", "3", "9"}:
                digit = classify_transcript(last_transcript)
            if not status:
                status = {"1": "attending", "2": "not_attending", "3": "unsure", "9": "callback_requested"}.get(digit, "no_response")
            if action != "hangup":
                fallback = render_script(scripts["no_response_script"], script_vars)
                if status == "attending_needs_headcount":
                    fallback = render_script(scripts["headcount_missing_script"], script_vars)
                elif digit == "1":
                    fallback = render_script(scripts["thanks_attending_script"], script_vars)
                elif digit == "2":
                    fallback = render_script(scripts["thanks_not_attending_script"], script_vars)
                elif digit == "3":
                    fallback = render_script(scripts["thanks_unsure_script"], script_vars)
                elif digit == "9":
                    fallback = render_script(scripts["thanks_callback_script"], script_vars)
            post_result(
                contact_id,
                attempt_id,
                digit,
                f"AI final: {decision.get('reason', '')}; greeting={greeting_transcript}; response={last_transcript}",
                amd_status,
                amd_cause,
                result_recording(full_call_recording, last_recording),
                last_transcript,
                status,
                decision_blob,
                trace_blob,
                party_size=party_fields["party_size"],
                party_kids=party_fields["party_kids"],
                party_friends=party_fields["party_friends"],
                party_family=party_fields["party_family"],
                party_details=party_fields["party_details"],
            )
            if action != "hangup":
                stream_text(f"ai-final-{digit or status}", decision_text(decision, fallback))
            return True
        if action != "speak_and_listen":
            decision = {"action": "complete", "status": "no_response", "reason": f"unsupported AI action {action}"}
            append_trace(trace, "unsupported_action", decision)
            continue

        turn += 1
        prompt_text = decision_text(decision, render_script(scripts["intro_script" if turn == 1 else "voice_prompt_script"], script_vars))
        prompt_file = make_prompt(f"ai-turn-{turn}", prompt_text)
        response_stage = str(decision.get("next_stage") or ("rsvp_response" if turn == 1 else "human_response"))
        last_response_stage = response_stage
        try:
            collect_digits = max(1, min(int(decision.get("collect_digits") or 1), 2))
        except (TypeError, ValueError):
            collect_digits = 1
        listen_timeout = int(decision.get("listen_ms") or listen_ms)
        escape_digits = stage_escape_digits(response_stage)
        prompt_response = agi(f"STREAM FILE {quote(prompt_file)} {quote(escape_digits)}")
        if agi_hung_up(prompt_response):
            hangup_status = "attending_needs_headcount" if response_stage == "attending_followup" else "no_response"
            hangup_digit = "1" if response_stage == "attending_followup" else ""
            hangup_decision = {"action": "complete", "status": hangup_status, "digit": hangup_digit, "reason": "caller hung up during AI prompt/listen", "source": "agi"}
            append_trace(trace, "caller_hangup", hangup_decision, last_transcript, "", last_recording)
            post_result(
                contact_id,
                attempt_id,
                hangup_digit,
                f"AI caller hangup during prompt/listen; greeting={greeting_transcript}; response={last_transcript}",
                amd_status,
                amd_cause,
                result_recording(full_call_recording, last_recording),
                last_transcript,
                hangup_status,
                json.dumps(hangup_decision, ensure_ascii=True, separators=(",", ":")),
                json.dumps(trace[-20:], ensure_ascii=True),
                party_details="Caller hung up before headcount." if response_stage == "attending_followup" else "",
            )
            return True

        digit = digit_from_agi_response(prompt_response, collect_digits, response_stage)
        recording_path = ""
        voice_recording = ""
        if not digit:
            silence_seconds = int(os.getenv("AI_HEADCOUNT_SILENCE_SECONDS", "3")) if response_stage == "attending_followup" else int(os.getenv("AI_RESPONSE_SILENCE_SECONDS", "2"))
            recording_path, voice_recording, record_response = record_clip_with_response(
                recording_dir,
                attempt_id,
                f"ai-turn-{turn}",
                listen_timeout,
                silence_seconds,
                "#",
            )
            if agi_hung_up(record_response):
                hangup_status = "attending_needs_headcount" if response_stage == "attending_followup" else "no_response"
                hangup_digit = "1" if response_stage == "attending_followup" else ""
                hangup_decision = {"action": "complete", "status": hangup_status, "digit": hangup_digit, "reason": "caller hung up during AI response recording", "source": "agi"}
                append_trace(trace, "caller_hangup", hangup_decision, last_transcript, "", last_recording)
                post_result(
                    contact_id,
                    attempt_id,
                    hangup_digit,
                    f"AI caller hangup during response recording; greeting={greeting_transcript}; response={last_transcript}",
                    amd_status,
                    amd_cause,
                    result_recording(full_call_recording, last_recording),
                    last_transcript,
                    hangup_status,
                    json.dumps(hangup_decision, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(trace[-20:], ensure_ascii=True),
                    party_details="Caller hung up before headcount." if response_stage == "attending_followup" else "",
                )
                return True
            digit = digit_from_agi_response(record_response, collect_digits, response_stage)

        if digit:
            if response_stage != "attending_followup" and digit == "1":
                decision = menu_digit_fast_path(digit, scripts, script_vars, listen_timeout)
            elif response_stage != "attending_followup" and digit in {"2", "3", "9"}:
                decision = menu_digit_fast_path(digit, scripts, script_vars, listen_timeout)
            else:
                payload = decision_payload(campaign_id, contact_id, attempt_id, contact_name, response_stage, turn, "", answer_class, digit)
                try:
                    decision = post_decision(payload)
                except Exception as exc:
                    decision = {"action": "complete", "status": "no_response", "reason": f"AI decision failed after DTMF: {exc}"}
            append_trace(trace, response_stage, decision, "", digit, voice_recording)
            continue

        transcript = maybe_transcribe(recording_path if voice_recording else "")
        last_transcript = transcript or last_transcript
        last_recording = voice_recording or last_recording
        payload = decision_payload(campaign_id, contact_id, attempt_id, contact_name, response_stage, turn, transcript, answer_class, "")
        try:
            decision = post_decision(payload)
        except Exception as exc:
            decision = {"action": "complete", "status": "no_response", "reason": f"AI decision failed after speech: {exc}"}
        append_trace(trace, response_stage, decision, transcript, "", voice_recording)

    fallback_status = "attending_needs_headcount" if last_response_stage == "attending_followup" else "no_response"
    fallback_digit = "1" if last_response_stage == "attending_followup" else ""
    fallback_decision = {"action": "complete", "status": fallback_status, "digit": fallback_digit, "reason": "AI max turns exhausted"}
    stream_text("ai-max-turns", render_script(scripts["headcount_missing_script" if fallback_status == "attending_needs_headcount" else "no_response_script"], script_vars))
    post_result(
        contact_id,
        attempt_id,
        fallback_digit,
        f"AI max turns exhausted; greeting={greeting_transcript}; response={last_transcript}",
        amd_status,
        amd_cause,
        result_recording(full_call_recording, last_recording),
        last_transcript,
        fallback_status,
        json.dumps(fallback_decision, ensure_ascii=True, separators=(",", ":")),
        json.dumps(trace[-20:], ensure_ascii=True),
        party_details="Headcount not captured before max turns." if fallback_status == "attending_needs_headcount" else "",
    )
    return True


def main() -> None:
    env = agi_read_env()
    campaign_id = get_variable("CAMPAIGN_ID", "default")
    contact_id = get_variable("CONTACT_ID", env.get("agi_arg_1", ""))
    attempt_id = get_variable("ATTEMPT_ID")
    full_call_recording = get_variable("FULL_CALL_RECORDING_FILE")
    contact_name = get_variable("CONTACT_NAME", "there")
    callback_number = os.getenv("CALLBACK_NUMBER", "")
    config = fetch_campaign_config(campaign_id)
    scripts = {key: config.get(key, value) for key, value in DEFAULT_SCRIPTS.items()}
    script_vars = {
        "campaign_id": campaign_id,
        "contact_name": contact_name or "there",
        "callback_number": callback_number,
        "party_size": "",
        "party_kids": "",
        "party_friends": "",
        "party_family": "",
        "party_details": "",
    }
    recording_dir = Path(os.getenv("VOICE_RECORDING_DIR", "/var/spool/asterisk/recording"))
    recording_dir.mkdir(parents=True, exist_ok=True)

    ai_observe = enabled(config.get("_ai_enabled", "1")) and enabled(os.getenv("AI_OBSERVE_ENABLED", "true"))
    greeting_transcript = ""
    greeting_recording = ""
    answer_class = ""
    if ai_observe:
        observe_ms = int_config(config, "_ai_observe_ms", 0, 0, 15000)
        if observe_ms > 0:
            greeting_transcript, greeting_recording, answer_class = observe_answer(recording_dir, attempt_id, observe_ms)
            amd_status = "MACHINE" if answer_class == "machine" else "HUMAN" if answer_class == "human" else "UNKNOWN"
            amd_cause = f"AI_OBSERVE:{answer_class}"
        else:
            answer_class = "unknown"
            amd_status = "UNKNOWN"
            amd_cause = "AI_FAST_START"
        handled = run_ai_flow(
            campaign_id,
            contact_id,
            attempt_id,
            contact_name,
            scripts,
            script_vars,
            recording_dir,
            amd_status,
            amd_cause,
            answer_class,
            greeting_transcript,
            greeting_recording,
            int_config(config, "_ai_listen_ms", 7000, 1000, 20000),
            int_config(config, "_ai_max_turns", 3, 1, 8),
            full_call_recording,
        )
        if handled:
            return
    else:
        agi("EXEC AMD")
        amd_status = get_variable("AMDSTATUS")
        amd_cause = get_variable("AMDCAUSE")

    legacy_flow(contact_id, attempt_id, scripts, script_vars, recording_dir, amd_status, amd_cause, greeting_transcript, greeting_recording, full_call_recording)


if __name__ == "__main__":
    main()
