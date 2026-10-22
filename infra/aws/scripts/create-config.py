#!/usr/bin/env python3
"""Generate a new installation's secret file locally; upload separately with AWS CLI.

Only Python's standard library is needed. Password input is never echoed and the
output is created exclusively with mode 0600. Do not regenerate on later releases.
"""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-email", required=True)
    parser.add_argument("--smoke-email", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    operator = args.operator_email.strip().lower()
    smoke = args.smoke_email.strip().lower()
    if "@" not in operator or "@" not in smoke or operator == smoke:
        parser.error("Provide two different email addresses for operator and release checks")
    password = getpass.getpass("Operator login password (at least 12 characters): ")
    if len(password) < 12 or password != getpass.getpass("Confirm operator password: "):
        parser.error("Passwords must match and contain at least 12 characters")
    config = {
        "OPERATOR_EMAIL": operator,
        "OPERATOR_PASSWORD": password,
        "SMOKE_EMAIL": smoke,
        "SMOKE_PASSWORD": secrets.token_urlsafe(32),
        "SMOKE_MCP_TOKEN": secrets.token_urlsafe(32),
        "APP_DB_PASSWORD": secrets.token_urlsafe(32),
        "MIGRATION_DB_PASSWORD": secrets.token_urlsafe(32),
        "COORDINATOR_DB_PASSWORD": secrets.token_urlsafe(32),
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "SECRETS_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    }
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
        json.dump(config, file, indent=2)
        file.write("\n")
    print(f"Created {args.output}; upload to Secrets Manager, then remove the local file.")


if __name__ == "__main__":
    main()
