BEGIN;
ALTER TABLE public.file_versions ADD CONSTRAINT completed_s3_version_required
    CHECK (storage_backend <> 's3' OR upload_status <> 'completed'
           OR (object_version IS NOT NULL AND btrim(object_version) <> '' AND object_version <> 'null'));
COMMIT;
