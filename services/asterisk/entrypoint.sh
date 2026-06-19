#!/bin/sh
set -eu

envsubst < /etc/asterisk/pjsip.conf.template > /etc/asterisk/pjsip.conf
if [ -z "${AVAYA_SIP_PASSWORD:-}" ] || [ "${AVAYA_SIP_PASSWORD}" = "change-me" ]; then
  sed -i '/^outbound_auth=avaya-auth$/d' /etc/asterisk/pjsip.conf
fi
if [ "${AVAYA_REGISTER:-true}" = "true" ]; then
  envsubst < /etc/asterisk/pjsip-registration.conf.template >> /etc/asterisk/pjsip.conf
fi
envsubst < /etc/asterisk/rtp.conf.template > /etc/asterisk/rtp.conf
mkdir -p /var/spool/asterisk/outgoing /var/spool/asterisk/outgoing_done /var/spool/asterisk/recording /var/lib/asterisk/sounds/generated /var/lib/asterisk/sounds/en /var/log/asterisk
ln -sfn /var/lib/asterisk/sounds/generated /var/lib/asterisk/sounds/en/generated
chown -R asterisk:asterisk /var/spool/asterisk /var/lib/asterisk/sounds/generated /var/log/asterisk
chown -h asterisk:asterisk /var/lib/asterisk/sounds/en/generated
chmod 0777 /var/spool/asterisk/outgoing

(sleep 4; asterisk -rx "pjsip set logger on" || true) &

exec asterisk -f -U asterisk -G asterisk -vvv
