-- B1, first migration. Existing file_index rows remain valid until B2 imports
-- and verifies the local files. Add NOT NULL references in a later migration
-- only after that backfill; directories and pending uploads stay nullable.
BEGIN;

CREATE TABLE public.files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    logical_path text NOT NULL CHECK (btrim(logical_path) <> ''),
    kind text NOT NULL DEFAULT 'file' CHECK (kind IN ('file', 'directory')),
    course_code text,
    current_version_id uuid,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, id),
    CHECK (kind = 'file' OR current_version_id IS NULL),
    FOREIGN KEY (user_id, course_code)
        REFERENCES public.courses(user_id, code) ON DELETE SET NULL (course_code)
);

CREATE UNIQUE INDEX files_active_path_key
    ON public.files(user_id, logical_path) WHERE deleted_at IS NULL;

CREATE TABLE public.file_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    file_id uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    storage_backend text NOT NULL DEFAULT 'local'
        CHECK (storage_backend IN ('local', 's3')),
    object_key text NOT NULL CHECK (btrim(object_key) <> ''),
    object_version text,
    size bigint CHECK (size >= 0),
    sha256 text,
    content_type text,
    upload_status text NOT NULL DEFAULT 'pending'
        CHECK (upload_status IN ('pending', 'completed', 'abandoned')),
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (user_id, file_id, id),
    UNIQUE (user_id, file_id, revision),
    FOREIGN KEY (user_id, file_id)
        REFERENCES public.files(user_id, id) ON DELETE CASCADE
);

ALTER TABLE public.files ADD CONSTRAINT files_current_version_fkey
    FOREIGN KEY (user_id, id, current_version_id)
    REFERENCES public.file_versions(user_id, file_id, id);

CREATE TABLE public.jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    file_id uuid NOT NULL,
    file_version_id uuid NOT NULL,
    job_type text NOT NULL CHECK (btrim(job_type) <> ''),
    pipeline_version text NOT NULL CHECK (btrim(pipeline_version) <> ''),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_run_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    execution_id uuid,
    error_code text,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, id),
    UNIQUE (file_version_id, job_type, pipeline_version),
    FOREIGN KEY (user_id, file_id, file_version_id)
        REFERENCES public.file_versions(user_id, file_id, id) ON DELETE CASCADE
);

CREATE INDEX jobs_file_idx ON public.jobs(user_id, file_id, created_at DESC);
CREATE INDEX jobs_waiting_idx ON public.jobs(next_run_at)
    WHERE status IN ('queued', 'retry_wait');
CREATE INDEX jobs_lease_idx ON public.jobs(lease_until) WHERE status = 'running';

CREATE TABLE public.job_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    job_id uuid NOT NULL,
    attempt_no integer NOT NULL CHECK (attempt_no > 0),
    execution_id uuid NOT NULL UNIQUE,
    worker_id text NOT NULL,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled', 'expired')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    error_code text,
    error_message text,
    UNIQUE (job_id, attempt_no),
    FOREIGN KEY (user_id, job_id)
        REFERENCES public.jobs(user_id, id) ON DELETE CASCADE
);

CREATE TABLE public.outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    job_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent')),
    available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    FOREIGN KEY (user_id, job_id)
        REFERENCES public.jobs(user_id, id) ON DELETE CASCADE
);

CREATE INDEX outbox_pending_idx ON public.outbox(available_at) WHERE status = 'pending';

ALTER TABLE public.file_index
    ADD COLUMN file_id uuid,
    ADD COLUMN file_version_id uuid,
    ADD CONSTRAINT file_index_version_pair_check
        CHECK ((file_id IS NULL) = (file_version_id IS NULL)),
    ADD CONSTRAINT file_index_file_version_fkey
        FOREIGN KEY (user_id, file_id, file_version_id)
        REFERENCES public.file_versions(user_id, file_id, id) ON DELETE CASCADE;

CREATE UNIQUE INDEX file_index_file_key
    ON public.file_index(user_id, file_id) WHERE file_id IS NOT NULL;

-- New tables use the runtime role's existing DML/default grants. User
-- authorization stays in application queries; do not add tenant RLS policies.
COMMIT;
