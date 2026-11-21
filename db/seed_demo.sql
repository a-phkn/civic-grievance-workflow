-- =====================================================================
-- DEMO DATA (Phase 1) - clearly demo, not real people or complaints.
-- The AI-proposal step (DFD 3.0) is not part of Phase 1, so complaint
-- 101 is loaded as already ACCEPTED with a demo proposal id.
--
-- Every demo account's password is  Demo@2026  (each row has its own
-- salt, so the stored Argon2id hashes all differ).
-- Run after schema.sql:  psql -d grievance -f seed_demo.sql
-- =====================================================================
BEGIN;

-- Agencies and task types (handoff: Water, Electricity, Roads)
INSERT INTO agency (agency_id, agency_name, description) VALUES
    (1, 'Water',       'Water supply and pipelines'),
    (2, 'Electricity', 'Power lines and electrical safety'),
    (3, 'Roads',       'Road surface and repairs');

INSERT INTO agency_task_type (agency_id, task_code, task_description, sla_hours) VALUES
    (1, 'PIPE_REPAIR',  'Repair a leaking or burst water pipe',      48),
    (2, 'SAFETY_CHECK', 'Make exposed or damaged power lines safe',  24),
    (3, 'ROAD_REPAIR',  'Resurface or patch a damaged road',         72);

-- The first ADMIN is created at database setup (design rule),
-- plus one demo user per role; one staff member per agency.
INSERT INTO app_user (user_id, name, email, password_hash, role, agency_id) VALUES
    (1, 'System Admin', 'admin@civic.demo', '$argon2id$v=19$m=65536,t=3,p=4$HAwTzKOj61i8f4kHYDYN6g$Q2LtyrrGzdGuQZtJXTygxxXhZLEOrZTOQ2pqp/D1uhQ', 'ADMIN', NULL),
    (2, 'Priya Citizen', 'citizen@civic.demo', '$argon2id$v=19$m=65536,t=3,p=4$/X3NDildWeuwiHOuTzzgZQ$tteSelX4ziVnjdbMBOMNxjKQfsgE+oXYhIhTlG6GhTY', 'CITIZEN', NULL),
    (3, 'Water Staff', 'water.staff@civic.demo', '$argon2id$v=19$m=65536,t=3,p=4$TwNuSHZpl8WWoC5jDXRVNQ$V9f5rvJU+3BzEUwWC8rNkoFALoIP9FoDyfsgTtCpoy8', 'AGENCY_STAFF', 1),
    (4, 'Electricity Staff', 'power.staff@civic.demo', '$argon2id$v=19$m=65536,t=3,p=4$HAyqLXZIYtfgsH1kP5NVmw$vN5WsYAz8ZdOMlZ9jTjpF1Sxc8GJmLok0W62AXFTM1s', 'AGENCY_STAFF', 2),
    (5, 'Roads Staff', 'roads.staff@civic.demo', '$argon2id$v=19$m=65536,t=3,p=4$HoT2ozfTZHFkWnDZIjytmg$xXl9nG33JpTus/19yI3GG0Fvel8Yl0jbZDO2ZetS2lY', 'AGENCY_STAFF', 3),
    (6, 'Ravi Reviewer', 'reviewer@civic.demo', '$argon2id$v=19$m=65536,t=3,p=4$5Fdn+5ghkVpc4oC3zmnQyg$HGye+2zefJPzHUNbkVspDUklX0825NrZVEe2+pIKpHI', 'REVIEWER', NULL);

-- Complaint 101, accepted exactly as DFD 4.4 does it, in one transaction:
-- complaint PENDING -> insert tasks -> insert dependencies -> ACCEPTED.
INSERT INTO complaint (complaint_id, citizen_id, description) VALUES
    (101, 2, 'DEMO: Burst water pipe has flooded the road outside 12th Main, '
             'and a street-light cable is lying in the water.');

-- Tasks 1 and 2 have no prerequisites -> READY_FOR_DISPATCH; the status
-- trigger sets ready_at and due_at (= now + SLA) at insert.
-- Task 3 waits for both -> BLOCKED, no deadline yet.
INSERT INTO task (task_id, complaint_id, agency_id, task_code, status) VALUES
    (1, 101, 1, 'PIPE_REPAIR',  'READY_FOR_DISPATCH'),
    (2, 101, 2, 'SAFETY_CHECK', 'READY_FOR_DISPATCH'),
    (3, 101, 3, 'ROAD_REPAIR',  'BLOCKED');

INSERT INTO task_dependency (prerequisite_task_id, dependent_task_id, complaint_id) VALUES
    (1, 3, 101),
    (2, 3, 101);

UPDATE complaint
   SET intake_status = 'ACCEPTED',
       accepted_proposal_id = '000000000000000000000101'   -- demo ObjectId
 WHERE complaint_id = 101;

INSERT INTO audit_log (complaint_id, user_id, action, old_status, new_status, reason) VALUES
    (101, 6, 'ACCEPT', 'PENDING', 'ACCEPTED', 'DEMO: workflow loaded as seed data');

-- Explicit ids were used above, so move each identity counter past them.
SELECT setval(pg_get_serial_sequence('agency',    'agency_id'),    (SELECT max(agency_id)    FROM agency));
SELECT setval(pg_get_serial_sequence('app_user',  'user_id'),      (SELECT max(user_id)      FROM app_user));
SELECT setval(pg_get_serial_sequence('complaint', 'complaint_id'), (SELECT max(complaint_id) FROM complaint));
SELECT setval(pg_get_serial_sequence('task',      'task_id'),      (SELECT max(task_id)      FROM task));

COMMIT;
