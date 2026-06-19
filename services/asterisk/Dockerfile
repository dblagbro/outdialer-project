FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends asterisk espeak-ng sox python3 python3-requests gettext-base ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY config/ /etc/asterisk/
COPY agi/rsvp_agi.py /usr/share/asterisk/agi-bin/rsvp_agi.py
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh /usr/share/asterisk/agi-bin/rsvp_agi.py \
    && mkdir -p /var/lib/asterisk/sounds/generated

ENTRYPOINT ["/entrypoint.sh"]
