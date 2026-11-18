"""Create private systemd settings for an existing Tailscale node."""
import argparse
import getpass
import os
import re
import secrets
from pathlib import Path

from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host', help='The VM’s full Tailscale DNS name, without https://')
    parser.add_argument('--username', default='owner')
    parser.add_argument('--output', type=Path, default=Path('/etc/python-job-agent.env'))
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]+\.ts\.net', args.host) or not re.fullmatch(r'[A-Za-z0-9_.-]+', args.username):
        parser.error('Use a valid .ts.net hostname and an alphanumeric username.')
    if args.output.exists():
        parser.error('The configuration already exists. Edit it in place to preserve keys.')
    password = getpass.getpass('Dashboard password (at least 12 characters): ')
    if len(password) < 12 or password != getpass.getpass('Repeat dashboard password: '):
        parser.error('Passwords must match and contain at least 12 characters.')
    values = {
        'JOB_AGENT_HOSTED': '1',
        'JOB_AGENT_HOST': args.host,
        'JOB_AGENT_USERNAME': args.username,
        'JOB_AGENT_PASSWORD_HASH': generate_password_hash(password),
        'JOB_AGENT_SESSION_KEY': secrets.token_hex(32),
        'JOB_AGENT_DATA_DIR': '/var/lib/python-job-agent',
    }
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as config:
        for name, value in values.items():
            config.write(f"{name}='{value}'\n")
    print(f'Configuration saved to {args.output}.')


if __name__ == '__main__':
    main()
