-- Initial multi-user database schema. Apply once to a fresh database.
-- Later timestamped migrations extend this baseline via scripts/run_migrations.py.

--
-- PostgreSQL database dump
--



SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: citext; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS citext WITH SCHEMA public;


--
-- Name: EXTENSION citext; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION citext IS 'data type for case-insensitive character strings';


--
-- Name: log_table_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.log_table_change() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
  payload jsonb;
  row_id text;
  course_code text;
  user_id uuid;
  kind text;
BEGIN
  -- Extract id (uuid or composite key) best-effort
  IF TG_OP = 'DELETE' THEN
    BEGIN row_id := (row_to_json(OLD)::jsonb->>'id'); EXCEPTION WHEN OTHERS THEN row_id := NULL; END;
    BEGIN course_code := (row_to_json(OLD)::jsonb->>'course_code'); EXCEPTION WHEN OTHERS THEN course_code := NULL; END;
    BEGIN user_id := (row_to_json(OLD)::jsonb->>'user_id')::uuid; EXCEPTION WHEN OTHERS THEN user_id := NULL; END;
    payload := jsonb_build_object(
      'table', TG_TABLE_NAME,
      'op', TG_OP,
      'id', row_id,
      'before', to_jsonb(OLD)
    );
  ELSE
    BEGIN row_id := (row_to_json(NEW)::jsonb->>'id'); EXCEPTION WHEN OTHERS THEN row_id := NULL; END;
    BEGIN course_code := (row_to_json(NEW)::jsonb->>'course_code'); EXCEPTION WHEN OTHERS THEN course_code := NULL; END;
    BEGIN user_id := (row_to_json(NEW)::jsonb->>'user_id')::uuid; EXCEPTION WHEN OTHERS THEN user_id := NULL; END;
    IF TG_OP = 'UPDATE' THEN
      payload := jsonb_build_object(
        'table', TG_TABLE_NAME,
        'op', TG_OP,
        'id', row_id,
        'before', to_jsonb(OLD),
        'after', to_jsonb(NEW)
      );
    ELSE
      payload := jsonb_build_object(
        'table', TG_TABLE_NAME,
        'op', TG_OP,
        'id', row_id,
        'after', to_jsonb(NEW)
      );
    END IF;
  END IF;

  kind := 'db:' || lower(TG_OP) || ':' || TG_TABLE_NAME;

  INSERT INTO public.events (kind, course_code, user_id, payload)
  VALUES (kind, course_code, user_id, payload);

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: search_files(text, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.search_files(q text, lim integer DEFAULT 20) RETURNS TABLE(path text, course_code text, size bigint, rank real, snippet text)
    LANGUAGE sql STABLE
    AS $$
  WITH query AS (
    SELECT websearch_to_tsquery('simple', q) AS tsq
  )
  SELECT
    f.path,
    f.course_code,
    f.size,
    ts_rank(f.search_vector, query.tsq) AS rank,
    ts_headline(
      'simple',
      f.text_content,
      query.tsq,
      'StartSel=<<,StopSel=>>,MaxFragments=2,MaxWords=15,MinWords=5,FragmentDelimiter=" … "'
    ) AS snippet
  FROM file_index f, query
  WHERE f.search_vector @@ query.tsq
  ORDER BY rank DESC
  LIMIT lim;
$$;


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO ''
    AS $$
begin
  new.updated_at := now();
  return new;
end;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: app_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_settings (
    display_name text,
    monogram text,
    institution text,
    semester_label text,
    semester_start date,
    semester_end date,
    timezone text DEFAULT 'America/New_York'::text,
    locale text DEFAULT 'en-US'::text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    theme text DEFAULT 'editorial'::text,
    totp_secret text,
    totp_enabled boolean DEFAULT false NOT NULL,
    user_id uuid NOT NULL
);


--
-- Name: auth_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_attempts (
    id bigint NOT NULL,
    ip text NOT NULL,
    ok boolean NOT NULL,
    user_agent text,
    at timestamp with time zone DEFAULT now(),
    kind text DEFAULT 'login'::text NOT NULL,
    CONSTRAINT auth_attempts_kind_check CHECK ((kind = ANY (ARRAY['login'::text, 'signup'::text, 'reset'::text, 'register'::text])))
);


--
-- Name: courses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.courses (
    code text NOT NULL,
    full_name text NOT NULL,
    short_name text,
    module_code text,
    ects integer,
    prof text,
    status_kind text,
    language text,
    color_hex text,
    folder_name text,
    exam_weight integer DEFAULT 100,
    exam_retries integer,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid NOT NULL
);


--
-- Name: deliverables; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deliverables (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    course_code text,
    kind text,
    name text NOT NULL,
    available_at timestamp with time zone,
    due_at timestamp with time zone NOT NULL,
    status text DEFAULT 'open'::text,
    local_path text,
    external_url text,
    weight_info text,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid NOT NULL
);


--
-- Name: email_verifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_verifications (
    token text NOT NULL,
    user_id uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    kind text NOT NULL,
    course_code text,
    payload jsonb,
    created_at timestamp with time zone DEFAULT now(),
    user_id uuid NOT NULL
);


--
-- Name: exams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exams (
    course_code text NOT NULL,
    scheduled_at timestamp with time zone,
    duration_min integer,
    location text,
    structure text,
    aids_allowed text,
    status text DEFAULT 'planned'::text,
    weight_pct integer DEFAULT 100,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid NOT NULL
);


--
-- Name: file_index; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.file_index (
    path text NOT NULL,
    course_code text,
    size bigint,
    sha256 text,
    text_content text,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, COALESCE(text_content, ''::text))) STORED,
    indexed_at timestamp with time zone DEFAULT now(),
    user_id uuid NOT NULL
);


--
-- Name: lectures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lectures (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    course_code text,
    number integer,
    held_on date,
    kind text DEFAULT 'lecture'::text,
    title text,
    summary text,
    attended boolean DEFAULT false,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid NOT NULL
);


--
-- Name: login_attempts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.login_attempts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: login_attempts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.login_attempts_id_seq OWNED BY public.auth_attempts.id;


--
-- Name: oauth_auth_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.oauth_auth_codes (
    code text NOT NULL,
    client_id text NOT NULL,
    redirect_uri text NOT NULL,
    code_challenge text NOT NULL,
    code_challenge_method text DEFAULT 'S256'::text NOT NULL,
    scope text,
    expires_at timestamp with time zone NOT NULL,
    used boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    user_id uuid NOT NULL
);


--
-- Name: oauth_clients; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.oauth_clients (
    client_id text NOT NULL,
    client_secret text,
    client_name text NOT NULL,
    redirect_uris text[] NOT NULL,
    token_endpoint_auth_method text DEFAULT 'none'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: oauth_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.oauth_tokens (
    token text NOT NULL,
    client_id text NOT NULL,
    scope text,
    expires_at timestamp with time zone,
    revoked boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    user_id uuid NOT NULL
);


--
-- Name: password_resets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_resets (
    token text NOT NULL,
    user_id uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: schedule_slots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_slots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    course_code text,
    kind text NOT NULL,
    weekday integer NOT NULL,
    start_time time without time zone NOT NULL,
    end_time time without time zone NOT NULL,
    room text,
    person text,
    starts_on date,
    ends_on date,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid NOT NULL,
    CONSTRAINT schedule_slots_weekday_check CHECK (((weekday >= 1) AND (weekday <= 7)))
);


--
-- Name: study_topics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.study_topics (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    course_code text,
    chapter text,
    name text NOT NULL,
    kind text,
    covered_on date,
    status text DEFAULT 'not_started'::text,
    confidence integer,
    last_reviewed_at timestamp with time zone,
    notes text,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    lecture_id uuid,
    description text,
    user_id uuid NOT NULL,
    CONSTRAINT study_topics_confidence_check CHECK (((confidence >= 0) AND (confidence <= 5)))
);


--
-- Name: tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    course_code text,
    title text NOT NULL,
    description text,
    due_at timestamp with time zone,
    status text DEFAULT 'open'::text,
    priority text DEFAULT 'med'::text,
    tags text[],
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid NOT NULL
);


--
-- Name: user_secrets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_secrets (
    user_id uuid NOT NULL,
    telegram_bot_token_enc bytea,
    telegram_chat_id text,
    telegram_webhook_secret_enc bytea,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid NOT NULL,
    email public.citext NOT NULL,
    password_hash text,
    display_name text DEFAULT 'User'::text NOT NULL,
    email_verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    totp_secret text,
    totp_enabled boolean DEFAULT false NOT NULL
);


--
-- Name: auth_attempts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_attempts ALTER COLUMN id SET DEFAULT nextval('public.login_attempts_id_seq'::regclass);


--
-- Name: app_settings app_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_pkey PRIMARY KEY (user_id);


--
-- Name: courses courses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.courses
    ADD CONSTRAINT courses_pkey PRIMARY KEY (user_id, code);


--
-- Name: deliverables deliverables_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deliverables
    ADD CONSTRAINT deliverables_pkey PRIMARY KEY (id);


--
-- Name: email_verifications email_verifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verifications
    ADD CONSTRAINT email_verifications_pkey PRIMARY KEY (token);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: exams exams_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exams
    ADD CONSTRAINT exams_pkey PRIMARY KEY (user_id, course_code);


--
-- Name: file_index file_index_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_index
    ADD CONSTRAINT file_index_pkey PRIMARY KEY (user_id, path);


--
-- Name: lectures lectures_course_kind_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lectures
    ADD CONSTRAINT lectures_course_kind_number_key UNIQUE (course_code, kind, number);


--
-- Name: lectures lectures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lectures
    ADD CONSTRAINT lectures_pkey PRIMARY KEY (id);


--
-- Name: auth_attempts login_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_attempts
    ADD CONSTRAINT login_attempts_pkey PRIMARY KEY (id);


--
-- Name: oauth_auth_codes oauth_auth_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_auth_codes
    ADD CONSTRAINT oauth_auth_codes_pkey PRIMARY KEY (code);


--
-- Name: oauth_clients oauth_clients_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_clients
    ADD CONSTRAINT oauth_clients_pkey PRIMARY KEY (client_id);


--
-- Name: oauth_tokens oauth_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_tokens
    ADD CONSTRAINT oauth_tokens_pkey PRIMARY KEY (token);


--
-- Name: password_resets password_resets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_pkey PRIMARY KEY (token);


--
-- Name: schedule_slots schedule_slots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_slots
    ADD CONSTRAINT schedule_slots_pkey PRIMARY KEY (id);


--
-- Name: study_topics study_topics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_topics
    ADD CONSTRAINT study_topics_pkey PRIMARY KEY (id);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: user_secrets user_secrets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_secrets
    ADD CONSTRAINT user_secrets_pkey PRIMARY KEY (user_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: file_index_course_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX file_index_course_idx ON public.file_index USING btree (course_code);


--
-- Name: file_index_search_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX file_index_search_idx ON public.file_index USING gin (search_vector);


--
-- Name: idx_auth_attempts_ip_kind_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_auth_attempts_ip_kind_at ON public.auth_attempts USING btree (ip, kind, at);


--
-- Name: idx_courses_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_courses_user_id ON public.courses USING btree (user_id);


--
-- Name: idx_deliverables_course; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_deliverables_course ON public.deliverables USING btree (course_code);


--
-- Name: idx_deliverables_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_deliverables_due ON public.deliverables USING btree (due_at);


--
-- Name: idx_deliverables_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_deliverables_user_id ON public.deliverables USING btree (user_id);


--
-- Name: idx_email_verifications_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_email_verifications_user_id ON public.email_verifications USING btree (user_id);


--
-- Name: idx_events_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_events_created ON public.events USING btree (created_at DESC);


--
-- Name: idx_events_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_events_user_id ON public.events USING btree (user_id);


--
-- Name: idx_exams_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exams_user_id ON public.exams USING btree (user_id);


--
-- Name: idx_file_index_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_file_index_user_id ON public.file_index USING btree (user_id);


--
-- Name: idx_lectures_course; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lectures_course ON public.lectures USING btree (course_code);


--
-- Name: idx_lectures_held_on; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lectures_held_on ON public.lectures USING btree (held_on);


--
-- Name: idx_lectures_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lectures_user_id ON public.lectures USING btree (user_id);


--
-- Name: idx_oauth_auth_codes_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_oauth_auth_codes_user_id ON public.oauth_auth_codes USING btree (user_id);


--
-- Name: idx_oauth_tokens_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_oauth_tokens_user_id ON public.oauth_tokens USING btree (user_id);


--
-- Name: idx_password_resets_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_password_resets_user_id ON public.password_resets USING btree (user_id);


--
-- Name: idx_schedule_slots_course; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_schedule_slots_course ON public.schedule_slots USING btree (course_code);


--
-- Name: idx_schedule_slots_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_schedule_slots_user_id ON public.schedule_slots USING btree (user_id);


--
-- Name: idx_study_topics_course; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_study_topics_course ON public.study_topics USING btree (course_code);


--
-- Name: idx_study_topics_lecture; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_study_topics_lecture ON public.study_topics USING btree (lecture_id);


--
-- Name: idx_study_topics_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_study_topics_status ON public.study_topics USING btree (status);


--
-- Name: idx_study_topics_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_study_topics_user_id ON public.study_topics USING btree (user_id);


--
-- Name: idx_tasks_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tasks_due ON public.tasks USING btree (due_at);


--
-- Name: idx_tasks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tasks_status ON public.tasks USING btree (status);


--
-- Name: idx_tasks_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tasks_user_id ON public.tasks USING btree (user_id);


--
-- Name: idx_user_secrets_telegram_chat_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_secrets_telegram_chat_id ON public.user_secrets USING btree (telegram_chat_id) WHERE (telegram_chat_id IS NOT NULL);


--
-- Name: oauth_auth_codes_expires_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX oauth_auth_codes_expires_idx ON public.oauth_auth_codes USING btree (expires_at);


--
-- Name: oauth_tokens_client_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX oauth_tokens_client_idx ON public.oauth_tokens USING btree (client_id);


--
-- Name: courses trg_log_change_courses; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_log_change_courses AFTER INSERT OR DELETE OR UPDATE ON public.courses FOR EACH ROW EXECUTE FUNCTION public.log_table_change();


--
-- Name: deliverables trg_log_change_deliverables; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_log_change_deliverables AFTER INSERT OR DELETE OR UPDATE ON public.deliverables FOR EACH ROW EXECUTE FUNCTION public.log_table_change();


--
-- Name: exams trg_log_change_klausuren; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_log_change_klausuren AFTER INSERT OR DELETE OR UPDATE ON public.exams FOR EACH ROW EXECUTE FUNCTION public.log_table_change();


--
-- Name: lectures trg_log_change_lectures; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_log_change_lectures AFTER INSERT OR DELETE OR UPDATE ON public.lectures FOR EACH ROW EXECUTE FUNCTION public.log_table_change();


--
-- Name: schedule_slots trg_log_change_schedule_slots; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_log_change_schedule_slots AFTER INSERT OR DELETE OR UPDATE ON public.schedule_slots FOR EACH ROW EXECUTE FUNCTION public.log_table_change();


--
-- Name: study_topics trg_log_change_study_topics; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_log_change_study_topics AFTER INSERT OR DELETE OR UPDATE ON public.study_topics FOR EACH ROW EXECUTE FUNCTION public.log_table_change();


--
-- Name: tasks trg_log_change_tasks; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_log_change_tasks AFTER INSERT OR DELETE OR UPDATE ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.log_table_change();


--
-- Name: courses trg_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_updated_at BEFORE UPDATE ON public.courses FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: deliverables trg_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_updated_at BEFORE UPDATE ON public.deliverables FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: exams trg_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_updated_at BEFORE UPDATE ON public.exams FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: lectures trg_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_updated_at BEFORE UPDATE ON public.lectures FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: schedule_slots trg_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_updated_at BEFORE UPDATE ON public.schedule_slots FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: study_topics trg_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_updated_at BEFORE UPDATE ON public.study_topics FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: tasks trg_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_updated_at BEFORE UPDATE ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: app_settings app_settings_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: courses courses_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.courses
    ADD CONSTRAINT courses_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: deliverables deliverables_user_course_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deliverables
    ADD CONSTRAINT deliverables_user_course_fkey FOREIGN KEY (user_id, course_code) REFERENCES public.courses(user_id, code) ON DELETE CASCADE;


--
-- Name: deliverables deliverables_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deliverables
    ADD CONSTRAINT deliverables_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: email_verifications email_verifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verifications
    ADD CONSTRAINT email_verifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: exams exams_user_course_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exams
    ADD CONSTRAINT exams_user_course_fkey FOREIGN KEY (user_id, course_code) REFERENCES public.courses(user_id, code) ON DELETE CASCADE;


--
-- Name: exams exams_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exams
    ADD CONSTRAINT exams_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: file_index file_index_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_index
    ADD CONSTRAINT file_index_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: lectures lectures_user_course_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lectures
    ADD CONSTRAINT lectures_user_course_fkey FOREIGN KEY (user_id, course_code) REFERENCES public.courses(user_id, code) ON DELETE CASCADE;


--
-- Name: lectures lectures_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lectures
    ADD CONSTRAINT lectures_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: oauth_auth_codes oauth_auth_codes_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_auth_codes
    ADD CONSTRAINT oauth_auth_codes_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.oauth_clients(client_id) ON DELETE CASCADE;


--
-- Name: oauth_auth_codes oauth_auth_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_auth_codes
    ADD CONSTRAINT oauth_auth_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: oauth_tokens oauth_tokens_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_tokens
    ADD CONSTRAINT oauth_tokens_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.oauth_clients(client_id) ON DELETE CASCADE;


--
-- Name: oauth_tokens oauth_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_tokens
    ADD CONSTRAINT oauth_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: password_resets password_resets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: schedule_slots schedule_slots_user_course_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_slots
    ADD CONSTRAINT schedule_slots_user_course_fkey FOREIGN KEY (user_id, course_code) REFERENCES public.courses(user_id, code) ON DELETE CASCADE;


--
-- Name: schedule_slots schedule_slots_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_slots
    ADD CONSTRAINT schedule_slots_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: study_topics study_topics_lecture_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_topics
    ADD CONSTRAINT study_topics_lecture_id_fkey FOREIGN KEY (lecture_id) REFERENCES public.lectures(id) ON DELETE SET NULL;


--
-- Name: study_topics study_topics_user_course_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_topics
    ADD CONSTRAINT study_topics_user_course_fkey FOREIGN KEY (user_id, course_code) REFERENCES public.courses(user_id, code) ON DELETE CASCADE;


--
-- Name: study_topics study_topics_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_topics
    ADD CONSTRAINT study_topics_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: tasks tasks_user_course_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_user_course_fkey FOREIGN KEY (user_id, course_code) REFERENCES public.courses(user_id, code) ON DELETE SET NULL (course_code);


--
-- Name: tasks tasks_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_secrets user_secrets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_secrets
    ADD CONSTRAINT user_secrets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: app_settings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.app_settings ENABLE ROW LEVEL SECURITY;

--
-- Name: app_settings app_settings_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY app_settings_user_isolation ON public.app_settings USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: auth_attempts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.auth_attempts ENABLE ROW LEVEL SECURITY;

--
-- Name: auth_attempts auth_attempts_allow_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY auth_attempts_allow_all ON public.auth_attempts USING (true) WITH CHECK (true);


--
-- Name: courses; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.courses ENABLE ROW LEVEL SECURITY;

--
-- Name: courses courses_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY courses_user_isolation ON public.courses USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: deliverables; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.deliverables ENABLE ROW LEVEL SECURITY;

--
-- Name: deliverables deliverables_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY deliverables_user_isolation ON public.deliverables USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: email_verifications email_verifications_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY email_verifications_user_isolation ON public.email_verifications USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;

--
-- Name: events events_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY events_user_isolation ON public.events USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: exams; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.exams ENABLE ROW LEVEL SECURITY;

--
-- Name: exams exams_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exams_user_isolation ON public.exams USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: file_index; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.file_index ENABLE ROW LEVEL SECURITY;

--
-- Name: file_index file_index_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY file_index_user_isolation ON public.file_index USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: lectures; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lectures ENABLE ROW LEVEL SECURITY;

--
-- Name: lectures lectures_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY lectures_user_isolation ON public.lectures USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: oauth_auth_codes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.oauth_auth_codes ENABLE ROW LEVEL SECURITY;

--
-- Name: oauth_auth_codes oauth_auth_codes_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY oauth_auth_codes_user_isolation ON public.oauth_auth_codes USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: oauth_clients; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.oauth_clients ENABLE ROW LEVEL SECURITY;

--
-- Name: oauth_clients oauth_clients_allow_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY oauth_clients_allow_all ON public.oauth_clients USING (true) WITH CHECK (true);


--
-- Name: oauth_tokens; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.oauth_tokens ENABLE ROW LEVEL SECURITY;

--
-- Name: oauth_tokens oauth_tokens_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY oauth_tokens_user_isolation ON public.oauth_tokens USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: password_resets password_resets_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY password_resets_user_isolation ON public.password_resets USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: schedule_slots; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.schedule_slots ENABLE ROW LEVEL SECURITY;

--
-- Name: schedule_slots schedule_slots_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY schedule_slots_user_isolation ON public.schedule_slots USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: study_topics; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.study_topics ENABLE ROW LEVEL SECURITY;

--
-- Name: study_topics study_topics_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY study_topics_user_isolation ON public.study_topics USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: tasks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;

--
-- Name: tasks tasks_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tasks_user_isolation ON public.tasks USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: user_secrets; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_secrets ENABLE ROW LEVEL SECURITY;

--
-- Name: user_secrets user_secrets_user_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_secrets_user_isolation ON public.user_secrets USING ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((user_id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- Name: users users_self_only; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY users_self_only ON public.users USING ((id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid)) WITH CHECK ((id = (( SELECT current_setting('app.user_id'::text, true) AS current_setting))::uuid));


--
-- PostgreSQL database dump complete
--



-- Initial operator account; deployment initialization sets its credentials.
INSERT INTO public.users (id, email, display_name, created_at)
VALUES ('00000000-0000-0000-0000-000000000001'::uuid,
        'operator@local', 'Operator', now())
ON CONFLICT (id) DO NOTHING;
