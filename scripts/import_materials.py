"""Import a downloaded directory through the versioned API (for n8n or an operator).

Requires INTERNAL_API_SECRET in the environment. No direct object key selection,
legacy-directory writes, or full-index rebuild is performed.
"""
import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
from uuid import UUID

import httpx


def import_directory(base_url, user_id, source, *, scheduled=False):
    secret = os.environ.get('INTERNAL_API_SECRET', '').strip()
    if not secret:
        raise ValueError('INTERNAL_API_SECRET is required')
    if not source.is_dir():
        raise ValueError('source directory is missing')
    base = base_url.rstrip('/') + '/api/internal/materials'
    headers = {'X-Internal-Secret': secret}
    report = {'user_id': str(user_id), 'written_count': 0, 'unchanged': 0, 'errors': []}
    with httpx.Client(timeout=120) as client:
        for path in sorted(source.rglob('*')):
            relative = path.relative_to(source)
            if any(part.startswith('.') for part in relative.parts) or path.is_dir():
                continue
            try:
                if path.is_symlink():
                    raise ValueError('symbolic links cannot be imported')
                data = path.read_bytes()
                mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                response = client.post(base + '/upload-url', headers=headers, json={
                    'user_id': str(user_id), 'path': relative.as_posix(), 'size': len(data),
                    'sha256': hashlib.sha256(data).hexdigest(), 'content_type': mime, 'scheduled': scheduled,
                })
                response.raise_for_status()
                target = response.json()
                if target.get('reused'):
                    report['unchanged'] += 1
                    continue
                version_base = f"{base}/{target['file_id']}/versions/{target['version_id']}"
                if target['storage_backend'] == 's3':
                    # Application authentication is never forwarded to object storage.
                    response = client.put(target['url'], headers=target['headers'], content=data)
                    response.raise_for_status()
                    object_version = response.headers.get('x-amz-version-id')
                    if not object_version or object_version == 'null':
                        raise ValueError('storage did not return a fixed object version')
                else:
                    response = client.put(version_base + '/content', params={'user_id': str(user_id)},
                                          headers={**headers, 'Content-Type': mime}, content=data)
                    response.raise_for_status()
                    object_version = None
                response = client.post(version_base + '/complete', headers=headers,
                                       json={'user_id': str(user_id), 'object_version': object_version})
                response.raise_for_status()
                report['written_count'] += 1
            except (OSError, ValueError, httpx.HTTPError):
                report['errors'].append({'path': relative.as_posix(), 'error': 'material import failed'})
    report['protocol'] = 'versioned-v1'
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--user-id', type=UUID, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--scheduled', action='store_true')
    args = parser.parse_args()
    result = import_directory(args.base_url, args.user_id, args.source_root, scheduled=args.scheduled)
    print(json.dumps(result))
    raise SystemExit(1 if result['errors'] else 0)
