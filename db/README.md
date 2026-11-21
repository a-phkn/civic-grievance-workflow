# Database (Phase 1, step 2)

PostgreSQL schema for the Civic Grievance Workflow System, built from the
submitted ER diagram, relational schema, DFDs and normalization domains.

## Files

| File | What it does |
| --- | --- |
| `schema.sql` | 7 tables, all keys and CHECKs, 4 triggers, 3 resolution functions, 2 views, 5 indexes |
| `seed_demo.sql` | Demo data: 3 agencies, 3 task types, 6 users (one per role, one staff per agency), complaint 101 with tasks 1-3 |
| `roles.sql` | Least-privilege login `grievance_app` for the Flask app |
| `test_schema.py` | 49 tests; each names the rule it checks and where that rule comes from |

## Run

```bash
createdb grievance
psql -d grievance -f schema.sql
psql -d grievance -f seed_demo.sql
psql -d grievance -v app_password="'choose-a-password'" -f roles.sql   # as a superuser
DATABASE_URL=postgresql://USER:PASS@localhost/grievance python test_schema.py
```

Re-running `schema.sql` drops and recreates everything, so run `roles.sql` again after it.
Demo password for every account: `Demo@2026`.

## Traceability: tables

| Table | ER | Keys (relational schema) | Participation -> NULL rule |
| --- | --- | --- | --- |
| agency | entity AGENCY | PK agency_id, UQ agency_name | - |
| app_user | entity APP_USER | PK user_id, UQ email, FK agency_id | HAS_USER partial -> agency_id nullable |
| complaint | entity COMPLAINT | PK complaint_id, FK citizen_id, UQ accepted_proposal_id | SUBMITS total -> citizen_id NOT NULL |
| agency_task_type | weak entity, identifying SUPPORTS | PK (agency_id, task_code) | owner key is part of PK |
| task | entity TASK | PK task_id, FK complaint_id, composite FK (agency_id, task_code), UQ (task_id, complaint_id) | GENERATES_TASK, DEFINES_TASK total -> NOT NULL |
| task_dependency | PREREQUISITE_OF / DEPENDENT_OF | PK (prerequisite, dependent), two composite FKs to TASK (task_id, complaint_id) | total on both -> NOT NULL |
| audit_log | entity AUDIT_LOG | PK audit_id, FKs task_id, complaint_id, user_id | all partial -> nullable |

## Allowed task status changes (from the state diagram and DFD 5.2-5.4)

| From | To | Made by | DFD |
| --- | --- | --- | --- |
| BLOCKED | READY_FOR_DISPATCH | unlock trigger | 5.3 |
| BLOCKED | BLOCKED_EXCEPTION | unlock trigger | 5.3 |
| BLOCKED | CANCELLED | reviewer | 5.4 |
| READY_FOR_DISPATCH | IN_PROGRESS | agency staff | 5.2 |
| READY_FOR_DISPATCH | CANCELLED | reviewer | 5.4 |
| IN_PROGRESS | COMPLETED | agency staff | 5.2 |
| IN_PROGRESS | FAILED | agency staff | 5.2 |
| FAILED | READY_FOR_DISPATCH | reviewer: retry | 5.4 |
| FAILED | CANCELLED | reviewer: cancel (task given up, with a reason) | 5.4 |
| BLOCKED_EXCEPTION | BLOCKED | resolution (after retry or waive) | 5.4 |
| BLOCKED_EXCEPTION | CANCELLED | reviewer: cancel | 5.4 |

Everything else is rejected. COMPLETED and CANCELLED are final.
A new task may only start as BLOCKED or READY_FOR_DISPATCH (DFD 4.4).

## Decisions made here that the design documents did not spell out

1. **Deadlines are set in one place.** The status trigger sets `ready_at = now` and
   `due_at = now + sla_hours` whenever a task enters READY_FOR_DISPATCH: at creation
   (fixes review gaps 2 and 4), at unlock, and on retry.
2. **Extra CHECKs:** email stored in lower case (so UNIQUE is case-insensitive),
   complaint description not blank, `due_at > ready_at`.
3. **Dependency insert trigger:** no new dependency once a complaint is ACCEPTED
   (design rule), and the dependent must be BLOCKED.
4. **Recovery after retry or waive** treats a prerequisite in BLOCKED_EXCEPTION as
   still failing, not only FAILED or CANCELLED. Without this, in a chain A -> B -> C,
   C would recover before B.
5. **Resolution (retry, waive, cancel) is written as PL/pgSQL functions**, so each is
   one all-or-nothing step and always writes its audit row with a reason (5+ chars).
6. **No ON DELETE CASCADE anywhere.** Nothing is deleted by accident, and audit
   history can never vanish with a parent row.
7. **`roles.sql` (extra):** the app logs in with a role that cannot edit or delete
   audit entries, delete complaints, or change the schema.
8. **FAILED -> CANCELLED (approved addition).** Without it, a failed task that is never
   retried keeps its complaint open forever. DFD 5.4 already lists cancel as a resolution;
   only the Reconciled Design state diagram gained an arrow.
9. **Types follow the frozen domains:** `TIMESTAMP` (not `TIMESTAMPTZ`), `VARCHAR(20)`
   statuses. `action` is `VARCHAR(30)`; the domains did not give it a type.
