"""Cutover refuses unlinked data and preserves verified indexes and full-text search."""
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from scripts import run_migrations as runner


MIGRATION = "20261022000002_file_index_versions.sql"
USER = "00000000-0000-0000-0000-000000000001"


def test_cutover_requires_backfill_and_preserves_verified_index(pg_url):
    name = "index_cutover_" + uuid4().hex
    dsn = pg_url.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(pg_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                runner.ensure_migrations_table(conn)
                migrations = sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql"))
                for migration in migrations:
                    if migration.name < MIGRATION:
                        runner.apply(conn, migration)
                conn.execute("INSERT INTO public.file_index(user_id,path,text_content) VALUES (%s,'old.pdf','quantum mechanics')", (USER,))
                cutover = next(m for m in migrations if m.name == MIGRATION)
                with pytest.raises(psycopg.errors.RaiseException, match="complete and verify the B2 import"):
                    runner.apply(conn, cutover)
                assert conn.execute("SELECT text_content FROM public.file_index").fetchone()[0] == "quantum mechanics"
                assert conn.execute("SELECT count(*) FROM public._migrations WHERE filename = %s", (MIGRATION,)).fetchone()[0] == 0
                file_id, version_id = uuid4(), uuid4()
                conn.execute("INSERT INTO public.files(id,user_id,logical_path) VALUES (%s,%s,'lecture.pdf')", (file_id, USER))
                conn.execute("""INSERT INTO public.file_versions(id,user_id,file_id,revision,object_key,upload_status)
                                VALUES (%s,%s,%s,1,'verified-object','completed')""", (version_id, USER, file_id))
                conn.execute("UPDATE public.file_index SET file_id = %s, file_version_id = %s", (file_id, version_id))
                runner.apply(conn, cutover)
                assert conn.execute("SELECT text_content,pipeline_version FROM public.file_index").fetchone() == ("quantum mechanics", "1")
                assert conn.execute("SELECT count(*) FROM public.file_index WHERE search_vector @@ to_tsquery('simple','quantum')").fetchone()[0] == 1
                with pytest.raises(psycopg.errors.NotNullViolation):
                    conn.execute("INSERT INTO public.file_index(user_id,path) VALUES (%s,'unlinked.pdf')", (USER,))
                # Path reuse is legal; stable file identity is the primary key.
                other_file, other_version = uuid4(), uuid4()
                conn.execute("INSERT INTO public.files(id,user_id,logical_path) VALUES (%s,%s,'other.pdf')", (other_file, USER))
                conn.execute("""INSERT INTO public.file_versions(id,user_id,file_id,revision,object_key)
                                VALUES (%s,%s,%s,1,'other-object')""", (other_version, USER, other_file))
                conn.execute("""INSERT INTO public.file_index(user_id,path,file_id,file_version_id)
                                VALUES (%s,'old.pdf',%s,%s)""", (USER, other_file, other_version))
                assert conn.execute("SELECT count(*) FROM public.file_index").fetchone()[0] == 2
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
