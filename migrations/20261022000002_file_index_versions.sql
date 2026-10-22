-- B1 cutover after every user's B2 import and verification has passed.
BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.file_index
               WHERE file_id IS NULL OR file_version_id IS NULL) THEN
        RAISE EXCEPTION 'Unlinked file indexes remain: complete and verify the B2 import before this migration';
    END IF;
END;
$$;

ALTER TABLE public.file_index
    DROP CONSTRAINT file_index_pkey,
    ALTER COLUMN file_id SET NOT NULL,
    ALTER COLUMN file_version_id SET NOT NULL,
    ADD CONSTRAINT file_index_pkey PRIMARY KEY (user_id, file_id),
    ADD COLUMN pipeline_version text NOT NULL DEFAULT '1'
        CHECK (btrim(pipeline_version) <> '');

DROP INDEX public.file_index_file_key;
-- Keep the existing text/vector columns and GIN index. path is retained as
-- internal metadata; names and paths shown to users come from files.
COMMIT;
