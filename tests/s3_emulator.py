"""Local-only Moto correction for standard REST checksum headers.

Moto 5.2 requires the SDK's algorithm hint even when the REST request includes
x-amz-checksum-sha256. AWS accepts the algorithm-specific header itself. Infer
that hint inside the emulator; production signing and verification stay unchanged.
This does not provide AWS signature, IAM or server-side checksum validation.
"""
from moto.s3.responses import S3Response


def rest_checksums():
    original = S3Response._get_checksum

    def checksum(self, response_headers):
        if self.headers.get("x-amz-checksum-sha256"):
            self.headers = dict(self.headers)
            self.headers["x-amz-sdk-checksum-algorithm"] = "SHA256"
        return original(self, response_headers)

    return checksum
