# Release Checklist

## Pre-Release

- Stop or pause any active campaign before packaging.
- Verify `.env` is not staged.
- Verify no recordings, logs, database dumps, or private Avaya details are staged.
- Run API syntax check.
- Run Docker build.
- Verify UI health endpoint.
- Verify Asterisk dialplan loads.
- Verify documentation PDF opens and renders.

## GitHub

Expected repository:

```text
dblagbro/outdialer-project
```

Recommended commands from a clean project directory:

```bash
git init
git branch -M main
git add .
git status --short
git commit -m "Initial outdialer project release"
gh repo create dblagbro/outdialer-project --public --source=. --remote=origin --push
git tag v0.1.0
git push origin v0.1.0
gh release create v0.1.0 --title "Outdialer Project v0.1.0" --notes-file docs/release-v0.1.0.md docs/outdialer-project-guide.pdf
```

If the repo already exists, use:

```bash
git remote add origin https://github.com/dblagbro/outdialer-project.git
git push -u origin main
git push origin v0.1.0
```

## Docker Hub

Expected images:

```text
dblagbro/outdialer-project-app:v0.1.0
dblagbro/outdialer-project-app:latest
dblagbro/outdialer-project-asterisk:v0.1.0
dblagbro/outdialer-project-asterisk:latest
```

Build and push:

```bash
docker build -t dblagbro/outdialer-project-app:v0.1.0 -t dblagbro/outdialer-project-app:latest services/outdialer
docker build -t dblagbro/outdialer-project-asterisk:v0.1.0 -t dblagbro/outdialer-project-asterisk:latest services/asterisk
docker push dblagbro/outdialer-project-app:v0.1.0
docker push dblagbro/outdialer-project-app:latest
docker push dblagbro/outdialer-project-asterisk:v0.1.0
docker push dblagbro/outdialer-project-asterisk:latest
```

## Post-Release

- Pull from GitHub into a temporary directory and confirm `docker compose config` works with `.env.example` copied to `.env`.
- Confirm Docker Hub image pull works.
- Update production by pulling the chosen tag or rebuilding from Git.
- Run one controlled test call.
