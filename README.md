# Outdialer Project

Dockerized SIP outdialer for small RSVP-style calling campaigns through an Avaya SIP environment.

This project runs a web UI, a campaign worker, PostgreSQL storage, and an Asterisk PJSIP media container. It supports multi-campaign contact lists, call windows, retry controls, caller ID settings, dial-prefix and number-format controls, SIP/log viewers, spoken-answer transcription hooks, local AI decision fallback, and optional Flowise integration.

## Current Production Shape

- Web UI and API: FastAPI on container port `8080`, normally exposed by the host on `8088` or by nginx under `/outdialer/`.
- Worker: polls enabled campaigns and writes Asterisk call files.
- Asterisk: originates SIP calls through the configured Avaya endpoint and runs the AGI call flow.
- Database: PostgreSQL with persistent Docker volume.
- Optional AI services: speech bridge, Flowise, and related services can be attached through the external Docker network named `docker_default`.

## Quick Start

```bash
cp .env.example .env
vi .env
docker compose up -d --build
curl http://localhost:8088/health
```

Open the UI:

```text
http://SERVER:8088/
```

Production deployments usually proxy this behind HTTPS, for example:

```text
https://YOUR-DOMAIN/outdialer/
```

## Required Configuration

Edit `.env` before first use. Do not commit `.env`.

Important settings:

- `POSTGRES_PASSWORD`: database password.
- `OUTDIALER_HTTP_PORT`: host port for the UI/API if nginx is not proxying internally.
- `CALLER_ID_NAME` and `CALLER_ID_NUMBER`: default caller identity.
- `AVAYA_SIP_HOST`: Avaya Session Manager or SIP target.
- `AVAYA_SIP_CONTACT_HOST`: host used in the Request-URI for outbound dialing.
- `AVAYA_SIP_USER_PHONE`: appends `;user=phone` to outbound SIP URIs when Avaya needs telephone-number routing.
- `AVAYA_FROM_DOMAIN`: SIP From domain accepted by Avaya.
- `AVAYA_SIP_USERNAME`, `AVAYA_SIP_AUTH_ID`, `AVAYA_SIP_PASSWORD`: SIP auth when registration/auth is required.
- `AVAYA_REGISTER`: `true` for SIP registration, `false` for trusted trunk/IP routing.
- `EXTERNAL_MEDIA_ADDRESS`, `EXTERNAL_SIGNALING_ADDRESS`, `LOCAL_NET`: NAT/RTP signaling behavior.
- `AI_GREETING_RECORD_MS`: set `0` for fast-start audio; higher values record before first speech.
- `DEEPGRAM_API_KEY`, `DEEPGRAM_TTS_MODEL`, `DEEPGRAM_STT_MODEL`: optional Deepgram TTS/STT integration.
- `WHISPER_BRIDGE_URL`: optional TTS/STT bridge endpoint.
- `FLOWISE_*`: optional Flowise chatflow settings.

## Documentation

Full documentation lives in `docs/`:

- `docs/OUTDIALER_PROJECT_GUIDE.md`: complete project guide with architecture and call flow.
- `docs/OPERATIONS.md`: day-to-day runbook.
- `docs/AVAYA_SIP.md`: Avaya/SIP routing and trace notes.
- `docs/AI_FLOW.md`: AI, speech, voicemail, Flowise, and prompt behavior.
- `docs/BACKUP_RESTORE.md`: backup, restore, and migration.
- `docs/RELEASE_CHECKLIST.md`: GitHub and Docker release checklist.
- `docs/outdialer-project-guide.pdf`: PDF guide generated from the documentation.

## CSV Format

```csv
name,phone,notes
Jane Example,+15555551212,Needs callback after 5 PM
John Example,5555,Internal test extension
```

The UI has a CSV template download, import, inline contact editing, reset/delete actions, and auto-refresh for the Contacts tab.

## Docker Images

Compose can build locally, or run from published tags:

- `dblagbro/outdialer-project-app:latest`
- `dblagbro/outdialer-project-asterisk:latest`

Override tags with:

```bash
OUTDIALER_APP_IMAGE=dblagbro/outdialer-project-app:VERSION
OUTDIALER_ASTERISK_IMAGE=dblagbro/outdialer-project-asterisk:VERSION
```

## Safety Notes

Use this only for contacts who expect or consent to the calls. Keep call windows reasonable, retries modest, and caller ID recognizable. Do not publish `.env`, recordings, database dumps, Avaya credentials, or internal network details.
