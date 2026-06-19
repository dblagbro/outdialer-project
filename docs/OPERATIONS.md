# Operations Runbook

## Daily UI Workflow

1. Open the UI through the protected HTTPS URL.
2. Pick the campaign from the header selector.
3. Review Dashboard readiness.
4. Confirm Settings:
   - caller ID name and number
   - dial prefix
   - number format
   - call window
   - max attempts
   - retry minutes
   - calls per worker tick
5. Import or edit contacts.
6. Confirm the Voice Script and AI Flow settings.
7. Start the campaign.
8. Watch Dashboard, Contacts, Call Log, Diagnostics, and Asterisk SIP Trace.
9. Stop the campaign when the test or calling window is complete.

## Contacts Tab

The Contacts tab supports:

- Add contact.
- Import CSV.
- Inline edit name, phone, status, and notes.
- Reset contact to pending with zero attempts.
- Delete contact and its attempts.
- Auto-refresh: Off, 5, 10, 15, 30, or 60 seconds.

Auto-refresh pauses while a form field is focused or changed, so edits are not overwritten during typing.

## Campaign State

The worker only dials when:

- campaign is enabled
- current local time is inside the configured call window
- contact status is `pending` or `no_response`
- contact attempts are below max attempts
- `next_call_at` is blank or in the past

## Useful Commands

Run from the project directory:

```bash
docker compose ps
docker compose logs -f outdialer-api outdialer-worker asterisk
docker compose exec asterisk asterisk -rx "pjsip show endpoints"
docker compose exec asterisk asterisk -rx "pjsip set logger on"
docker compose exec asterisk asterisk -rx "dialplan show birthday-rsvp"
curl http://localhost:8088/health
curl http://localhost:8088/status
```

## Start And Stop Campaigns From CLI

```bash
curl -X POST -d campaign_id=default http://localhost:8088/campaign/start
curl -X POST -d campaign_id=default http://localhost:8088/campaign/stop
```

The UI is preferred for normal operation because it makes the state visible.

## Logs And Exports

Use the UI for:

- contact export
- call log export
- diagnostic event export
- Asterisk SIP trace export

Asterisk log volume:

```text
asterisk_logs:/var/log/asterisk
```

Recordings volume:

```text
asterisk_recordings:/var/spool/asterisk/recording
```

## Upgrade Procedure

1. Stop the campaign.
2. Back up PostgreSQL and `.env`.
3. Pull latest code or image tags.
4. Rebuild/recreate services:

```bash
docker compose pull
docker compose up -d --build
```

5. Check health and UI.
6. Confirm settings persisted.
7. Run one controlled test contact before broad dialing.

## Rollback Procedure

1. Stop campaign.
2. Revert to previous Git tag or image tag.
3. Restore database only if schema/data was changed and rollback requires it.
4. Recreate services.
5. Check health and verify UI.

## Common Operational Problems

### Campaign Started But Nothing Dials

Check:

- call window and timezone
- contact statuses
- max attempts
- next retry time
- worker container logs
- campaign enabled flag in Dashboard

### Calls Queue But Fail In SIP

Check:

- Call Log last SIP response.
- Asterisk SIP Trace tab.
- Avaya Session Manager trace.
- Dial prefix and normalization.
- Caller ID/From domain.
- Request-URI target host.

### First Audio Is Slow

Recommended fast settings:

```text
Observe Milliseconds = 0
Listen Milliseconds = 4000
Max Turns = 1
TTS_TIMEOUT_SECONDS=2
```

If prompts were changed, first-time TTS generation can still add delay. It will be cached after generation.
