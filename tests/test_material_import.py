"""The directory adapter never forwards application credentials to S3."""
import httpx

from scripts import import_materials


def test_directory_import_separates_storage_credentials_and_confirms_fixed_version(tmp_path, monkeypatch):
    (tmp_path / 'notes.txt').write_bytes(b'content')
    monkeypatch.setenv('INTERNAL_API_SECRET', 'private-integration-key')
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == 'storage.example.test':
            assert 'x-internal-secret' not in request.headers
            assert request.content == b'content'
            return httpx.Response(200, headers={'x-amz-version-id': 'fixed-version'})
        assert request.headers['x-internal-secret'] == 'private-integration-key'
        if request.url.path.endswith('upload-url'):
            return httpx.Response(200, json={'file_id': 'file', 'version_id': 'version', 'storage_backend': 's3',
                'url': 'https://storage.example.test/object', 'headers': {'Content-Type': 'text/plain'}})
        assert request.url.path.endswith('/complete')
        assert b'fixed-version' in request.content
        return httpx.Response(200, json={'job_id': 'job'})

    original = httpx.Client
    monkeypatch.setattr(import_materials.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handle)))
    result = import_materials.import_directory('https://study.example.test', 'owner', tmp_path)
    assert result['written_count'] == 1 and not result['errors'] and len(requests) == 3
