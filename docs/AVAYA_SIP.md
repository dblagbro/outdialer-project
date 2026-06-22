# Avaya SIP Integration

## Goal

Asterisk sends outbound SIP INVITEs to Avaya Session Manager or an Avaya SIP routing target. Avaya then routes the call to the internal extension, PSTN gateway, or carrier according to Avaya configuration.

## Required Avaya Concepts

Exact screens vary by Avaya version, but the routing shape is usually:

- SIP Entity for the Asterisk/outdialer host, or registration credentials for a SIP user.
- Entity Link between Session Manager and the outdialer SIP entity if trusted IP routing is used.
- Routing Policy that sends the desired dial pattern toward the correct Avaya side.
- Dial Pattern matching the outdialer dialed digits.
- Communication Manager trunk or route pattern able to complete the call.
- Allowed/expected From domain and caller ID behavior.

## Registration Mode

Use this when Asterisk registers as a SIP user/extension:

```text
AVAYA_REGISTER=true
AVAYA_SIP_USERNAME=...
AVAYA_SIP_AUTH_ID=...
AVAYA_SIP_PASSWORD=...
```

Asterisk appends `pjsip-registration.conf.template` to the generated PJSIP config.

## Trusted IP / Trunk Mode

Use this when Avaya trusts the Asterisk host as a SIP peer:

```text
AVAYA_REGISTER=false
```

Avaya must route calls from the outdialer host by IP/SIP entity/link/routing policy. If Avaya challenges the INVITE and no auth is configured, calls will fail.

## Request-URI, To, And From

The worker builds:

```text
PJSIP/avaya/sip:DIALED_NUMBER@AVAYA_SIP_CONTACT_HOST:5060;user=phone
```

This leads to SIP like:

```text
INVITE sip:DIALED_NUMBER@AVAYA_SIP_CONTACT_HOST:5060;user=phone SIP/2.0
To: <sip:DIALED_NUMBER@AVAYA_SIP_CONTACT_HOST;user=phone>
From: "CALLER_ID_NAME" <sip:CALLER_ID_NUMBER@AVAYA_FROM_DOMAIN>
P-Asserted-Identity: <sip:CALLER_ID_NUMBER@AVAYA_FROM_DOMAIN>
```

The Request-URI host should be a SIP listener, normally Session Manager/SM100 or another Avaya SIP entity. It should not be a domain controller or a server that does not run SIP. `AVAYA_SIP_USER_PHONE=true` appends `;user=phone`, which helps Avaya treat the URI user as a telephone number instead of an internal SIP name.

## Dial Prefix And Long Distance

The app separates number formatting from dial prefix:

1. Number format processes the contact phone field.
2. Dial prefix is prepended afterward.

Examples:

| Contact input | Number format | Dial prefix | Dialed |
| --- | --- | --- | --- |
| `8455551212` | `nanp_1` | blank | `18455551212` |
| `8455551212` | `nanp_1` | `9` | `918455551212` |
| `8455551212` | `strip_only` | `91` | `918455551212` |
| `5555` | `strip_only` | blank | `5555` |

Do not assume `9 + 1` is always required. Configure it per campaign based on Avaya route patterns.

## Trace Strategy

### Asterisk Side

Use the UI Asterisk SIP Trace tab or:

```bash
docker compose exec asterisk asterisk -rx "pjsip set logger on"
docker compose logs -f asterisk
```

Look for:

- INVITE Request-URI
- To
- From
- P-Asserted-Identity
- Route
- Contact
- SDP connection address
- SIP response code

### Avaya Side

Use Session Manager tracing from the Avaya management host. Start trace before starting the campaign or before resetting a contact. Compare:

- Source IP: outdialer/Asterisk host.
- Destination IP: Session Manager/SM100 SIP listener.
- Request-URI host.
- From domain.
- Response code and reason.

## Common SIP Failures

### 403 Forbidden, Invalid From Domain

Avaya rejected the From domain.

Fix:

- Set `AVAYA_FROM_DOMAIN` to a domain Avaya accepts.
- Adjust Session Manager adaptation/domain settings.
- Confirm caller ID number is permitted.

### 404 Not Found, No Route Available

Avaya accepted the SIP message but did not know how to route the dialed number.

Fix:

- Verify dial prefix and number format.
- Check Session Manager dial pattern.
- Check route policy and Communication Manager route pattern.

### INVITE Goes To Wrong IP

Fix:

- Set `AVAYA_SIP_HOST` to the Avaya SIP listener used for identify/registration.
- Set `AVAYA_SIP_CONTACT_HOST` to the host that should appear in Request-URI.
- Keep `AVAYA_SIP_USER_PHONE=true` when Avaya only routes the call correctly with telephone-number SIP URI handling.
- Do not point SIP to domain controllers or non-SIP infrastructure.

### No Audio

Check:

- RTP port mapping.
- `EXTERNAL_MEDIA_ADDRESS`.
- `EXTERNAL_SIGNALING_ADDRESS`.
- `LOCAL_NET`; do not include the Avaya LAN unless that is intentionally local.
- Codec compatibility: current endpoint allows `ulaw` and `alaw`.
- Firewall between Avaya media resources and the Docker host.
