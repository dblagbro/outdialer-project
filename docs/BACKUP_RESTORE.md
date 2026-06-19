# Backup And Restore

## What To Back Up

Required:

- PostgreSQL data.
- `.env` file, stored securely outside Git.
- Any production nginx config, htpasswd file, and certificate references.

Optional:

- Asterisk recordings.
- Asterisk logs.
- Exported contacts and call logs from the UI.

Do not publish backups to GitHub or Docker images.

## PostgreSQL Backup

Run from the project directory:

```bash
mkdir -p backups
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" > backups/outdialer-$(date +%Y%m%d-%H%M%S).sql
```

If shell environment variables are not available on the host, use the values from `.env`.

## PostgreSQL Restore

Stop API/worker/Asterisk first:

```bash
docker compose stop outdialer-api outdialer-worker asterisk
```

Restore:

```bash
cat backups/outdialer.sql | docker compose exec -T postgres psql -U outdialer -d outdialer
```

Restart:

```bash
docker compose up -d
```

## Environment Backup

```bash
install -m 700 -d backups/private
cp .env backups/private/.env.$(date +%Y%m%d-%H%M%S)
chmod 600 backups/private/.env.*
```

Keep this directory private and outside Git.

## Recordings And Logs

Docker named volumes can be archived when needed:

```bash
docker run --rm -v outdialer_asterisk_recordings:/data -v "$PWD/backups:/backup" alpine tar czf /backup/recordings.tgz -C /data .
docker run --rm -v outdialer_asterisk_logs:/data -v "$PWD/backups:/backup" alpine tar czf /backup/asterisk-logs.tgz -C /data .
```

Volume names can vary if the Compose project name is different. Use:

```bash
docker volume ls | grep outdialer
```

## Migration To A New Host

1. Install Docker and Docker Compose.
2. Copy the project directory.
3. Copy `.env` securely.
4. Restore PostgreSQL dump.
5. Restore optional recordings/logs.
6. Confirm firewall ports:
   - HTTP or nginx HTTPS
   - SIP `5060` TCP/UDP as configured
   - RTP range `10000-10100` UDP by default
7. Start services.
8. Run one internal test call.
