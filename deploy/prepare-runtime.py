#!/usr/bin/env python3
"""Prepare a private runtime file on an explicitly reviewed application node."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from cloudops_config import load


def runtime(document, fetch):
    """Fetch without displaying values; return only the approved Docker env format."""
    identity = fetch('sts', 'get-caller-identity')
    prefix = f"arn:aws:sts::{document['AWS_ACCOUNT_ID']}:assumed-role/{document['APP_ROLE_NAME']}/"
    if identity.get('Account') != document['AWS_ACCOUNT_ID'] or not identity.get('Arn', '').startswith(prefix):
        raise ValueError('application instance role/account mismatch')
    database = json.loads(fetch('secretsmanager', 'get-secret-value', '--secret-id', document['DB_SECRET_NAME'])['SecretString'])
    if not isinstance(database, dict):
        raise ValueError('invalid database secret schema')
    name = database.get('dbname', database.get('database'))
    if not all(isinstance(database.get(k), str) and database[k] for k in ('host', 'username', 'password')) or not isinstance(name, str) or not name:
        raise ValueError('invalid database secret schema')
    port = int(database.get('port', 3306))
    if not 0 < port < 65536:
        raise ValueError('invalid database port')
    key = fetch('secretsmanager', 'get-secret-value', '--secret-id', document['SESSION_SECRET_NAME'])['SecretString']
    if not isinstance(key, str) or len(key) < 32 or any(c in key for c in ('\n', '\r', '\0')):
        raise ValueError('invalid stable signing secret')
    values = {'FLASK_ENV':'production', 'SECRET_KEY':key, 'SESSION_COOKIE_SECURE':document['SESSION_COOKIE_SECURE'],
              'USE_AWS_SECRETS':'true', 'AWS_SECRET_NAME':document['DB_SECRET_NAME'], 'AWS_REGION':document['AWS_REGION'],
              'ENABLE_LAB_FAILURE_ENDPOINTS':'false'}
    return ''.join(f'{k}={v}\n' for k,v in values.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('/etc/cloudops/runtime.env'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('run through an authorized root SSM session')
    try:
        document = load()
        def fetch(*arguments):
            result = subprocess.run(['aws', *arguments, '--region', document['AWS_REGION'], '--output', 'json'],
                                    check=True, capture_output=True, text=True)
            return json.loads(result.stdout)
        content = runtime(document, fetch)
        if args.output.exists() or args.output.is_symlink():
            raise ValueError('runtime file already exists; review it privately instead of overwriting it')
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if args.output.parent.is_symlink():
            raise ValueError('runtime directory must not be a symlink')
        fd, name = tempfile.mkstemp(prefix='.runtime-', dir=args.output.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic no-clobber publication; never overwrite a concurrent administrator's file.
            os.link(name, args.output)
        finally:
            Path(name).unlink(missing_ok=True)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        parser.exit(1, 'Runtime preparation failed; inspect permissions and secret schema privately. Values withheld.\n')
    print('Runtime prepared root:root 0600; secret values withheld. No application container changed.')


if __name__ == '__main__':
    main()
