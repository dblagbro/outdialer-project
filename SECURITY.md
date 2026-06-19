# Security

## Sensitive Files

Never commit or publish:

- `.env` or any copied environment file with real values.
- PostgreSQL volume data.
- Asterisk recordings.
- Asterisk logs from real calls.
- Avaya usernames, passwords, auth IDs, domains, routes, or internal IP maps.
- htpasswd files or nginx private keys/certificates.

This repository includes `.gitignore` and Docker ignore files to help prevent accidental publication, but operators remain responsible for reviewing release contents.

## Web Access

The app itself does not implement user login. Production deployments should place it behind nginx HTTPS and HTTP basic authentication, SSO, VPN, or another access-control layer.

Recommended nginx controls:

- HTTPS only.
- Basic auth or equivalent authentication.
- `proxy_set_header X-Forwarded-Proto https`.
- Restricted access to trusted networks where appropriate.

## SIP And Call Data

SIP traces and call logs can contain phone numbers and routing metadata. Export only the minimum needed for troubleshooting, and redact before sharing publicly.

## Abuse Prevention

Use the system only for expected, consented calls. Configure recognizable caller ID, reasonable call windows, modest retry limits, and a human callback option.
