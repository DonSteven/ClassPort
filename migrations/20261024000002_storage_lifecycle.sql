BEGIN;

ALTER TABLE public.file_versions
    ADD COLUMN upload_expires_at timestamptz NOT NULL DEFAULT (now() + interval '1 hour'),
    ADD COLUMN retired_at timestamptz,
    ADD COLUMN cleanup_checked_at timestamptz NOT NULL DEFAULT '1970-01-01 00:00:00+00',
    ADD COLUMN read_expires_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN content_state text NOT NULL DEFAULT 'available'
        CHECK (content_state IN ('available', 'retiring', 'purged'));

UPDATE public.file_versions v SET retired_at = now()
FROM public.files f WHERE f.user_id = v.user_id AND f.id = v.file_id
    AND v.upload_status = 'completed' AND v.id IS DISTINCT FROM f.current_version_id;

CREATE TABLE public.storage_migrations (
    user_id uuid NOT NULL,
    file_id uuid NOT NULL,
    file_version_id uuid PRIMARY KEY,
    source_key text NOT NULL,
    target_bucket text NOT NULL,
    target_key text NOT NULL,
    target_version text,
    status text NOT NULL DEFAULT 'copying' CHECK (status IN ('copying', 'completed')),
    retain_until timestamptz NOT NULL,
    last_error text,
    completed_at timestamptz,
    FOREIGN KEY (user_id, file_id, file_version_id)
        REFERENCES public.file_versions(user_id, file_id, id) ON DELETE CASCADE
);

CREATE TABLE public.storage_cleanup (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    file_id uuid NOT NULL,
    file_version_id uuid NOT NULL,
    storage_backend text NOT NULL CHECK (storage_backend IN ('local', 's3')),
    bucket text NOT NULL DEFAULT '',
    object_key text NOT NULL,
    object_version text NOT NULL DEFAULT '',
    reason text NOT NULL CHECK (reason IN ('content', 'orphan', 'migration_source')),
    not_before timestamptz NOT NULL,
    completed_at timestamptz,
    UNIQUE (storage_backend, bucket, object_key, object_version),
    UNIQUE (user_id, file_id, file_version_id, id),
    CHECK (storage_backend <> 's3' OR (bucket <> '' AND object_version NOT IN ('', 'null'))),
    FOREIGN KEY (user_id, file_id, file_version_id)
        REFERENCES public.file_versions(user_id, file_id, id) ON DELETE CASCADE
);

ALTER TABLE public.jobs
    DROP CONSTRAINT jobs_file_version_id_job_type_pipeline_version_key,
    ADD COLUMN cleanup_id uuid,
    ADD CONSTRAINT jobs_cleanup_target_fkey
        FOREIGN KEY (user_id, file_id, file_version_id, cleanup_id)
        REFERENCES public.storage_cleanup(user_id, file_id, file_version_id, id),
    ADD CONSTRAINT jobs_cleanup_type_check
        CHECK ((job_type = 'storage_cleanup') = (cleanup_id IS NOT NULL));

CREATE UNIQUE INDEX jobs_processing_key
    ON public.jobs(file_version_id, job_type, pipeline_version) WHERE cleanup_id IS NULL;
CREATE UNIQUE INDEX jobs_cleanup_key ON public.jobs(cleanup_id) WHERE cleanup_id IS NOT NULL;

CREATE TABLE public.material_sync_settings (
    user_id uuid PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
    paused boolean NOT NULL DEFAULT false
);

COMMIT;
