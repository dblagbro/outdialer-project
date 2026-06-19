# Changelog

## v0.1.0 - Initial Project Release

- Added multi-campaign web UI with Dashboard, Contacts, Call Log, Asterisk SIP Trace, Diagnostics, Settings, AI Flow, Voice Script, and Campaigns tabs.
- Added contact CSV import/export, inline editing, reset/delete, and auto-refresh controls.
- Added Asterisk PJSIP outdialing through Avaya SIP with caller ID, P-Asserted-Identity, P-Preferred-Identity, dial prefix, and number normalization controls.
- Added call log exports with SIP To/From/Route, last SIP response, AMD, transcript, AI decision, AI trace, recording link, and message.
- Added local AI decision loop, fast-start audio mode, speech bridge TTS/STT hooks, voicemail transcript detection, and optional Flowise provider settings.
- Added nginx-friendly `/outdialer/` deployment support through relative links and reverse-proxy-safe UI paths.
- Added full documentation set and generated PDF guide.
- Added Docker image names for app and Asterisk services.
