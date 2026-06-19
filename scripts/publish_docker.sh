#!/bin/sh
set -eu

VERSION="${1:-v0.1.0}"
APP_IMAGE="${OUTDIALER_APP_IMAGE_BASE:-dblagbro/outdialer-project-app}"
ASTERISK_IMAGE="${OUTDIALER_ASTERISK_IMAGE_BASE:-dblagbro/outdialer-project-asterisk}"

docker build -t "${APP_IMAGE}:${VERSION}" -t "${APP_IMAGE}:latest" services/outdialer
docker build -t "${ASTERISK_IMAGE}:${VERSION}" -t "${ASTERISK_IMAGE}:latest" services/asterisk

docker push "${APP_IMAGE}:${VERSION}"
docker push "${APP_IMAGE}:latest"
docker push "${ASTERISK_IMAGE}:${VERSION}"
docker push "${ASTERISK_IMAGE}:latest"
