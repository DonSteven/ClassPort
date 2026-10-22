BEGIN;
ALTER TABLE public.jobs ADD COLUMN request_id text;
COMMIT;
