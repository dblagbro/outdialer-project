# Outdialer Project v0.1.0

Initial documented release of the Dockerized SIP outdialer.

## Highlights

- Docker Compose project with FastAPI web UI, worker, Asterisk, PostgreSQL, and Redis.
- Avaya SIP/PJSIP integration with configurable target host, From domain, caller ID, dial prefix, and number format.
- Multi-campaign UI with contact import/export, inline editing, call logs, diagnostics, Asterisk SIP trace viewer, and settings.
- Fast-start AI call flow with local RSVP classification, voicemail transcript detection, optional speech bridge, and optional Flowise chatflow provider.
- Full documentation set plus generated PDF guide.

## Docker Images

- `dblagbro/outdialer-project-app:v0.1.0`
- `dblagbro/outdialer-project-app:latest`
- `dblagbro/outdialer-project-asterisk:v0.1.0`
- `dblagbro/outdialer-project-asterisk:latest`

## Security

This release intentionally excludes `.env`, call recordings, logs, database dumps, nginx certs, htpasswd files, and real Avaya credentials.
