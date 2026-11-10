"""Test a locally built API image with synthetic data and no external network."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid


def run(args, capture=False):
    return subprocess.run(args, check=True, timeout=180, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--postgres-image', default='postgres@sha256:e013e867e712fec275706a6c51c966f0bb0c93cfa8f51000f85a15f9865a28cb')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    contract = root / 'services/outdialer/tests/api_contract.py'
    assert contract.is_file()
    image = json.loads(run(['docker', 'image', 'inspect', args.image], capture=True))[0]['Id']
    run(['docker', 'pull', args.postgres_image])
    pg = json.loads(run(['docker', 'image', 'inspect', args.postgres_image], capture=True))[0]['Id']
    prefix = 'outdialer-test-' + uuid.uuid4().hex[:12]
    network, db, api = prefix, prefix + '-db', prefix + '-api'
    created = []
    try:
        run(['docker', 'network', 'create', '--internal', network])
        created.append(('network', network))
        run(['docker', 'run', '-d', '--name', db, '--network', network, '--network-alias', 'isolated-db',
             '--memory', '512m', '--pids-limit', '256', '--tmpfs', '/var/lib/postgresql/data:size=256m',
             '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=isolated', pg])
        created.append(('container', db))
        for _ in range(60):
            if subprocess.run(['docker', 'exec', db, 'pg_isready', '-U', 'postgres', '-d', 'isolated'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Isolated database did not become ready')
        run(['docker', 'run', '-d', '--name', api, '--network', network, '--memory', '512m',
             '--pids-limit', '128', '--security-opt', 'no-new-privileges=true', '--tmpfs', '/recordings:size=16m',
             '-e', 'DATABASE_URL=postgresql+psycopg://postgres@isolated-db/isolated', '-e', 'APP_ROLE=api',
             '-e', 'OUTDIALER_ISOLATED_TEST=yes', '-v', str(contract) + ':/tmp/contract.py:ro', image])
        created.append(('container', api))
        for _ in range(60):
            if subprocess.run(['docker', 'exec', api, 'python', '-c',
                               'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/health",timeout=2)'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Isolated API did not become ready')
        run(['docker', 'exec', '-e', 'PYTHONPATH=/app', api, 'python', '/tmp/contract.py'])
        run(['docker', 'exec', api, 'python', '-c', 'from app.db import init_db; init_db()'])
        run(['docker', 'exec', api, 'python', '-c',
             'import urllib.request,json; d=json.load(urllib.request.urlopen("http://127.0.0.1:8080/status")); assert d["total_contacts"]==1 and d["total_attempts"]==0 and not d["campaign_enabled"]; print("schema_idempotence_passed")'])
    finally:
        for kind, name in reversed(created):
            run(['docker', 'rm', '-f', name] if kind == 'container' else ['docker', 'network', 'rm', name])


if __name__ == '__main__':
    main()
