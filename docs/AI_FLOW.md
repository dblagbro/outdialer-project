# AI Flow, Speech, And Voicemail

## Modes

The app supports:

- Local rule fallback: deterministic RSVP classification.
- Flowise provider: sends call state to a configured Flowise prediction endpoint.
- Speech bridge: optional TTS and STT service used by Asterisk AGI.

## Fast-Start Mode

Recommended default:

```text
Observe Milliseconds = 0
```

This makes the call speak immediately after answer. It avoids waiting for pre-prompt audio recording, transcription, and AI classification.

Tradeoff: voicemail is detected after response recording rather than before first prompt.

## Pre-Observe Mode

Set Observe Milliseconds above zero when you want the app to listen before speaking:

```text
Observe Milliseconds = 3000
```

This can help identify voicemail greetings before the intro prompt, but it delays first audio.

## Local RSVP Classification

The local fallback maps:

- DTMF `1` or yes/attending phrases to `attending`.
- DTMF `2` or no/not-attending phrases to `not_attending`.
- DTMF `3` or maybe/unsure phrases to `unsure`.
- DTMF `9` or callback phrases to `callback_requested`.

Voicemail-like transcript markers map to `voicemail_left`.

## Flowise Contract

Flowise should return one JSON object. Valid actions:

- `speak_and_listen`
- `mark_rsvp`
- `leave_voicemail`
- `complete`
- `hangup`

Recommended response shape:

```json
{
  "action": "mark_rsvp",
  "digit": "1",
  "status": "attending",
  "reason": "caller clearly said they will attend",
  "confidence": 0.95,
  "text": "Thank you. We have you marked as attending. Goodbye.",
  "listen_ms": 4000,
  "hangup_after": true
}
```

If Flowise fails, the API falls back to local classification.

## Prompt Editing

Use the Voice Script tab to edit:

- Intro Script
- Voicemail Script
- Voice Answer Prompt
- Thank You: Attending
- Thank You: Not Attending
- Thank You: Unsure
- Thank You: Callback
- No Response

The AI Flow tab controls:

- AI enabled
- provider
- observe/listen timing
- max turns
- Flowise URL and chatflow ID
- event context
- system prompt
- builder notes

## TTS/STT Behavior

Asterisk AGI tries Deepgram first for TTS and STT when `DEEPGRAM_API_KEY` is set. If Deepgram is unavailable, it falls back to the speech bridge and then local `espeak-ng` for TTS.

Useful settings:

```text
DEEPGRAM_API_KEY=
DEEPGRAM_TTS_MODEL=aura-2-apollo-en
DEEPGRAM_STT_MODEL=nova-3
WHISPER_BRIDGE_URL=http://whisper-bridge:9000
WHISPER_BRIDGE_TOKEN=change-me
TTS_TIMEOUT_SECONDS=2
```

Prompt files are cached by provider/model/text hash under:

```text
/var/lib/asterisk/sounds/generated
```

Changing script text creates a new generated prompt.

## Logging

Call attempts include:

- transcript
- AI decision
- AI trace
- recording filename
- final status
- message

Diagnostic events include every AI decision payload and result summary. Redact before sharing logs publicly.
