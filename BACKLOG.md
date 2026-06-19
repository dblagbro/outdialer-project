# Outdialer Project Backlog

## Documentation and Repository Packaging

- Completed in the initial project release:
  - Full operator/developer documentation for "outdialer-project".
  - PDF guide with architecture and SIP/call-flow diagrams.
  - Docker deployment, backup/restore, Avaya Session Manager integration, nginx/HTTPS/auth, campaign operation, SIP trace troubleshooting, and recovery steps.
  - Clean Compose packaging with sample `.env`, sample CSV, persistent volumes, Docker image names, and upgrade notes.

## Future Enhancements

- Add first-class authentication management in the UI instead of relying only on nginx/htpasswd.
- Add explicit Flowise chatflow export/import examples once the final production chatflow is chosen.
- Add GitHub Actions for lint/build/publish after repository secrets are configured.
- Add automated end-to-end SIP simulator tests that do not call real phones.
