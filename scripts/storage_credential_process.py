"""SDK credential_process: print only the isolated short-lived storage grant."""
import json
from pathlib import Path
import sys


def credentials(path=Path("/run/classport-storage/credentials.json")):
    data = json.loads(path.read_text())
    keys = {"Version", "AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration"}
    if set(data) != keys or data["Version"] != 1 or not all(data[key] for key in keys):
        raise ValueError("invalid storage credentials")
    return data


if __name__ == "__main__":
    try:
        print(json.dumps(credentials()))
    except Exception:
        print("Storage credentials are unavailable", file=sys.stderr)
        raise SystemExit(1) from None
