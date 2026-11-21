-- =====================================================================
-- Database-level least privilege (run once as a superuser, after
-- schema.sql). The Flask app connects as grievance_app, never as the
-- owner, so even a bug or an injection in the app cannot drop tables,
-- change the schema, or rewrite history.
--
--   psql -d grievance -v app_password="'choose-a-strong-one'" -f roles.sql
-- =====================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grievance_app') THEN
        CREATE ROLE grievance_app LOGIN;
    END IF;
END $$;
ALTER ROLE grievance_app PASSWORD :app_password;

REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM grievance_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT  USAGE  ON SCHEMA public TO grievance_app;

-- Normal read/write on the working tables. No DELETE anywhere except
-- TASK_DEPENDENCY, which the "waive" resolution needs (DFD 5.4).
GRANT SELECT, INSERT, UPDATE ON agency, agency_task_type, app_user, complaint, task
      TO grievance_app;
GRANT SELECT, INSERT, DELETE ON task_dependency TO grievance_app;

-- AUDIT_LOG is append-only for the app: it can add and read entries,
-- but never edit or delete one.
GRANT SELECT, INSERT ON audit_log TO grievance_app;

GRANT SELECT ON v_task_overdue, v_complaint_progress TO grievance_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO grievance_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO grievance_app;
