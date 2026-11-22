# Security and validation

Rubric row: *Front-end design with a user-friendly GUI and **security validation***.
Every measure below says what it stops, where it is in the code, which test proves it,
and the source it follows ([S1] etc., listed at the end; every link was opened on
2026-11-21 to confirm it says what we cite it for). Test names are in `tests/test_app.py` (app) or
`db/test_schema.py` (database). Results: `docs/test-results/`.

Principle: **the server decides, the database double-checks.** Nothing in the browser
(hidden fields, disabled buttons, JavaScript) is trusted. Forms use `novalidate`, so
every message the user sees comes from the server, and a request crafted by hand meets
the same rules as one from the page.

## 1. Role-wise access

| Measure | Stops | Where | Proved by | Source |
| --- | --- | --- | --- | --- |
| `@role_required(...)` on every protected route; role read from APP_USER on each request | A citizen opening staff, reviewer or admin pages | `app/auth.py` `role_required`; every route in `citizen.py`, `staff.py`, `reviewer.py`, `admin.py` | `test_each_role_sees_only_its_own_pages` (all 4 roles x 4 areas), screenshot 08 | [S1] deny by default, check every request |
| Denied attempts are written to AUDIT_LOG as `ACCESS_DENIED` | Silent probing | `role_required`, `staff.update_status` | same test checks the audit row | [S11] log access-control failures |
| Not logged in -> login page | Anonymous access | Flask-Login `login_required` inside `role_required` | `test_anonymous_is_sent_to_login` | [S1] |
| Citizen sees only own complaints; someone else's id gives the same 404 as a missing one | Reading other citizens' complaints, guessing ids | `citizen.complaint_detail` (`WHERE citizen_id = me`) | `test_citizen_cannot_open_another_citizens_complaint` | [S1] ownership check |
| A reviewer's rejection reason is shown only to the citizen who owns the complaint; the reject form warns the reviewer that the citizen will read it | Leaking a reason to other users; internal notes reaching the citizen by surprise | `citizen.complaint_detail` (reason read only after the ownership check), reviewer reject hint | `test_citizen_sees_why_complaint_was_rejected`, `test_citizen_cannot_open_another_citizens_complaint` | [S1] ownership check |
| Staff agency comes from APP_USER, never from the form or URL; another agency's task -> 403 + audit | Staff changing another agency's task (DFD 5.1, 5.2) | `staff.task_list`, `staff.update_status` | `test_staff_cannot_update_another_agencys_task` | [S1], [S2] trusted server-side data |
| Registration always creates CITIZEN; a `role` field sent by hand is ignored | Self-promotion to admin | `auth.register` (role is a constant in the SQL) | `test_register_cannot_choose_a_role` | [S1] |
| Photos stored outside `/static`, served only to the owner or a reviewer, and only if MongoDB lists that file for that complaint | Reading photos by guessing URLs | `citizen.photo` | `test_photo_upload_accepted_and_private` | [S8] store outside webroot, [S1] |
| Database login `grievance_app` has only the rights it needs: no UPDATE/DELETE on audit_log, no DELETE on complaints, no schema changes | A bug or injection in the app wiping history | `db/roles.sql` | `db/test_schema.py` app-role tests | [S6] least privilege, [S15] GRANT |
| MongoDB runs with authorization on; the app logs in as `grievance_app` with readWrite on the `grievance` database only (cannot read other databases or create users) | Anyone on the machine reading or changing complaint text and AI proposals | `db/mongo_users.py`, `config.MONGO_URL` | `test_mongodb_needs_a_login_and_app_user_is_limited` | [S18] |


## 2. Authentication

| Measure | Stops | Where | Proved by | Source |
| --- | --- | --- | --- | --- |
| Passwords hashed with **Argon2id** (64 MiB, 3 passes, 4 lanes; library defaults, RFC 9106 low-memory profile) | Cracking leaked hashes | `auth.py` `PasswordHasher()`; demo hashes in `seed_demo.sql` | `test_passwords_are_argon2id_and_never_plain` | [S3], [S19] |
| Hash upgraded after login if parameters change | Old weak hashes staying forever | `ph.check_needs_rehash` in `auth.login` | (library behaviour) | [S20] |
| 5 wrong passwords in 15 minutes lock the account for 15 minutes (counted from AUDIT_LOG) | Password guessing | `auth._recent_failures`; limits in `config.py` | `test_login_locks_after_five_failures` | [S4] lockout, [S11] |
| Same message for unknown email and wrong password; unknown email still runs one Argon2 check | Finding out which emails are registered (by message or by response time) | `auth.login` `_DUMMY_HASH` | `test_unknown_email_and_wrong_password_look_the_same` | [S4] generic errors |
| Password rules: 8-128 characters, at least one letter and one number; confirm field must match | Trivial passwords | `forms.strong_password`, `RegisterForm` | `test_register_validation_messages`, screenshot 02 | [S4], [S2] |
| Session cleared at login; cookie HttpOnly, SameSite=Lax, Secure when `SESSION_COOKIE_SECURE=1`; form tokens expire after 30 minutes; Flask-Login `session_protection="strong"` | Session fixation, cookie theft by script | `auth.login`, `config.py` | `test_security_headers` (cookie flags) | [S5], [S13] |
| Login expires after 30 idle minutes; each request renews it, and Flask checks the cookie's age on the server | A forgotten logged-in browser being used later | `config.PERMANENT_SESSION_LIFETIME`, `session.permanent` in `auth.login` | `test_idle_session_expires` | [S5] idle timeout, [S13] |
| After login, always go to the role's home page; a `?next=` URL is never followed | Open redirect to a fake site | `auth.login` | `test_next_parameter_is_never_followed` | [S9] |
| Logout only by POST with CSRF token | Another site logging you out with a link | `auth.logout` | `test_logout_requires_post` | [S10] |


## 3. Attacks through input

| Measure | Stops | Where | Proved by | Source |
| --- | --- | --- | --- | --- |
| CSRF token on every form (Flask-WTF `CSRFProtect`); missing or wrong token -> 400 "Form expired or invalid" | Another site submitting forms as you | `app/__init__.py`; `form.hidden_tag()` in every template | `test_post_without_csrf_token_is_rejected`, screenshot 21 | [S10] |
| All SQL uses `%s` parameters (psycopg sends values separately from the query) | SQL injection | every `conn.execute` | `test_sql_injection_in_login_fails`, `test_sql_injection_text_is_stored_as_plain_text` | [S6], [S14] |
| Jinja auto-escaping; the `safe` filter is never used | Stored XSS (script in a complaint) | all templates | `test_script_in_complaint_is_escaped` | [S7], [S21] |
| Content-Security-Policy `default-src 'self'` with no inline scripts or styles | XSS even if escaping were missed | `app/__init__.py` `security_headers` | `test_security_headers` | [S7] CSP as defence in depth |
| `X-Frame-Options: DENY`, `frame-ancestors 'none'` | Clickjacking | same | same | [S12] |
| `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cache-Control: no-store` when logged in | Content sniffing, URL leaks, private pages left in the browser cache | same | same | [S13] |
| Debug mode off in `run.py` | The Werkzeug debugger letting anyone run code | `run.py` | (read the file) | [S24] |


## 4. File upload (complaint photo)

| Check | Stops | Where | Proved by | Source |
| --- | --- | --- | --- | --- |
| Extension must be jpg / jpeg / png | Scripts and other files | `ComplaintForm.photo` `FileAllowed`; `citizen._save_photo` | `test_wrong_extension_is_rejected` | [S8] |
| First bytes must match JPEG or PNG ("magic bytes") | A renamed `.exe` or `.html` | `citizen.SIGNATURES`, `_save_photo` | `test_renamed_non_image_is_rejected` | [S8] signature check (used with the other checks, as it advises) |
| 5 MB limit for the whole request (`MAX_CONTENT_LENGTH`) -> 413 page | Filling the disk / memory | `config.py`, 413 handler | `test_upload_over_5mb_is_refused` | [S8] |
| File saved under a random name (`uuid4`), never the user's file name | Path tricks (`../../`), overwriting, guessable URLs | `_save_photo` | `test_photo_upload_accepted_and_private` | [S8] application-generated names |
| Stored outside `/static`; served by a checked route; `nosniff` header | Direct download by anyone | `config.UPLOAD_FOLDER`, `citizen.photo` | same | [S8] |


## 5. Validation: form rule = database rule

Each form rule mirrors a rule already in the frozen normalization domains or the
schema, so the user gets a clear message first, and the database still refuses bad data
if the app is bypassed (tested in `db/test_schema.py`).

| Field | Form rule (`app/forms.py`) | Database rule (`db/schema.sql`) | Source |
| --- | --- | --- | --- |
| Email | required, valid format, max 255, stored lower case | `VARCHAR(255) UNIQUE`, CHECK `email = lower(email)` | [S22], [S16] |
| Name | 2-100, letters, spaces, `. ' -` | `VARCHAR(100) NOT NULL` | [S22], [S16] |
| Complaint description | 20-2,000 characters, trimmed | `NOT NULL`, CHECK not blank | [S22], [S16] |
| Task status (staff) | select offering only the legal next steps from the current status | trigger `fn_task_status_rules`: only the 11 allowed transitions | [S2] enforce business-rule flows, [S25] |
| Reviewer reason | 5-500 characters | `fn_require_reason` (5+ characters) | [S22] |
| Task code | 2-30, `^[A-Z][A-Z0-9_]*$`, upper-cased | part of PK `(agency_id, task_code)` | [S22], [S16] |
| SLA hours | whole number 1-8,760 | CHECK `sla_hours > 0` | [S22], [S16] |
| Staff account agency | required for agency staff, must be empty for a reviewer | CHECK `chk_user_agency` | [S16] CHECK |
| Duplicate email / agency | friendly message on `UniqueViolation` | UNIQUE constraints | [S16] UNIQUE |

Proved by: `test_register_validation_messages`, `test_duplicate_email_message`,
`test_complaint_too_short`, `test_admin_sla_must_be_positive`,
`test_admin_staff_needs_agency`, `test_staff_cannot_skip_a_step` (a hand-made request for
READY -> COMPLETED is refused), screenshots 02, 03, 15, 20.

## 6. Integrity and audit

| Measure | Where | Proved by | Source |
| --- | --- | --- | --- |
| A complaint is kept in both PostgreSQL and MongoDB, or in neither: the PostgreSQL transaction stays open until MongoDB has the document; on failure the Mongo doc and photo are removed and the user sees "could not be saved" | `citizen.new_complaint` | `test_complaint_written_to_postgres_and_mongodb`, `test_mongodb_failure_rolls_back_postgres` | [S28] [S29]: two stores cannot share one transaction, so a failed step is undone by a compensating action (here: roll back PostgreSQL, delete the Mongo doc and photo) |
| The MongoDB copy holds no name or email (only text, photo names, ids), ready for the NLP step | `citizen.new_complaint` | `test_complaint_written_to_postgres_and_mongodb` | [S30] data minimisation: the NLP step needs only the text, so personal data is not copied there |
| Two staff change the same task at once: the request re-reads the status and only offers the legal next steps from it, and the `UPDATE ... WHERE status = <status just read>` changes nothing if another request got in between, so the second person is refused ("not allowed from <new status>", or "changed while you were looking at it" if the two requests overlap) instead of overwriting | `staff.update_status` | `test_stale_status_change_is_refused` (page shows IN_PROGRESS, someone else completes the task, the "Report a failure" press is refused and the completion stands). The millisecond overlap inside one request is left to PostgreSQL's documented behaviour [S26], not a timing test | [S26] in Read Committed the second UPDATE waits and re-checks its WHERE on the new row; [S27] optimistic check: detect the conflict and refuse instead of locking |
| Two prerequisites completing at the same moment still unlock the dependent once (`FOR UPDATE`) | trigger `fn_task_unlock` | `db/test_schema.py` concurrent unlock test | [S17] SELECT ... FOR UPDATE |
| Every status change, login, failed login, denied access, reviewer decision and admin change is written to AUDIT_LOG; the app cannot edit or delete it | `db.audit`, triggers, `roles.sql` | `test_admin_sla_change_is_audited`, role tests | [S11] |
| Secrets come from environment variables, not the code; with no `SECRET_KEY` set, a random one is made at start-up, never a fixed default | `config.py` | (read the file) | [S2] keep credentials out of code |

## 7. Known limits (said openly)

- **HTTPS** is not set up for the local demo. In a real deployment it would sit behind
  HTTPS with `SESSION_COOKIE_SECURE=1`.
- `python run.py` uses Flask's development server. A real deployment would use a WSGI
  server such as gunicorn.
- The **lockout can be misused** to lock a known user out for 15 minutes. That is the
  usual trade-off of per-account lockout (OWASP mentions it); an IP-based limit would be
  the next step.
- Both databases listen only on 127.0.0.1 and the demo passwords (`app-password`) sit in
  the config defaults. A real deployment would set them through environment variables.
- After 5 wrong passwords a **registered** email gets the "Too many failed attempts"
  message and an unknown email does not, so someone patient could learn whether an
  email is registered. Hiding it would mean telling real users "wrong password" while
  they are locked out; we chose the clear message.
- The two-database write is undone by a **compensating delete**, which is best effort: if
  PostgreSQL fails to commit and MongoDB also goes down before the delete, an orphan
  MongoDB document can remain. It is harmless: the app only looks up `complaints_raw` by a
  complaint id it read from PostgreSQL, and PostgreSQL never hands out that id again
  (a value taken from a sequence is not reused when the transaction aborts [S31]; we checked it on the COMPLAINT id: after a rolled-back insert took 102, the next insert got 103).
  This is the known cost of compensation instead of a distributed transaction [S28].
- Registration has no CAPTCHA or email verification in this phase.

## Sources

Opened and checked on 2026-11-21.

- [S1] [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
- [S2] [OWASP Secure Coding Practices checklist](https://owasp.org/www-project-secure-coding-practices-quick-reference-guide/stable-en/02-checklist/05-checklist.html)
- [S3] [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [S4] [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [S5] [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [S6] [OWASP SQL Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html)
- [S7] [OWASP Cross Site Scripting Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)
- [S8] [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)
- [S9] [OWASP Unvalidated Redirects and Forwards Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Unvalidated_Redirects_and_Forwards_Cheat_Sheet.html)
- [S10] [OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [S11] [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
- [S12] [OWASP Clickjacking Defense Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Clickjacking_Defense_Cheat_Sheet.html)
- [S13] [Flask: Security Considerations](https://flask.palletsprojects.com/en/stable/web-security/)
- [S14] [psycopg 3: Passing parameters to SQL queries](https://www.psycopg.org/psycopg3/docs/basic/params.html)
- [S15] [PostgreSQL 16: GRANT](https://www.postgresql.org/docs/16/sql-grant.html)
- [S16] [PostgreSQL 16: Constraints](https://www.postgresql.org/docs/16/ddl-constraints.html)
- [S17] [PostgreSQL 16: Explicit Locking](https://www.postgresql.org/docs/16/explicit-locking.html)
- [S18] [MongoDB manual: SCRAM authentication (create the user administrator, then enable access control)](https://www.mongodb.com/docs/manual/tutorial/configure-scram-client-authentication/)
- [S19] [RFC 9106: Argon2 (64 MiB, t=3, p=4 profile)](https://www.rfc-editor.org/rfc/rfc9106.html)
- [S20] [argon2-cffi API (check_needs_rehash)](https://argon2-cffi.readthedocs.io/en/stable/api.html)
- [S21] [Jinja API: autoescaping](https://jinja.palletsprojects.com/en/stable/api/)
- [S22] [OWASP Input Validation Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)
- [S23] (Cross-reference, not a source) Reconciled Design "Complaint submission across both databases" and the handoff doc "Only complaint text goes to the NLP API" record these decisions; S28 to S30 are why they are sound
- [S24] [Flask: Debugging application errors (do not enable the debugger in production)](https://flask.palletsprojects.com/en/stable/debugging/)
- [S25] [PostgreSQL 16: Trigger functions in PL/pgSQL (OLD/NEW, abort by raising an error)](https://www.postgresql.org/docs/16/plpgsql-trigger.html)
- [S26] [PostgreSQL 16: Transaction isolation, Read Committed ("the WHERE clause is re-evaluated")](https://www.postgresql.org/docs/16/transaction-iso.html)
- [S27] [Martin Fowler: Optimistic Offline Lock](https://martinfowler.com/eaaCatalog/optimisticOfflineLock.html)
- [S28] [Microsoft Azure Architecture Center: Compensating Transaction pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction)
- [S29] [microservices.io: Saga pattern (local transactions undone by compensating transactions)](https://microservices.io/patterns/data/saga.html)
- [S30] [GDPR Article 5(1)(c): data minimisation](https://gdpr-info.eu/art-5-gdpr/)
- [S31] [PostgreSQL 16: Sequence functions (a nextval value is not reclaimed if the transaction aborts)](https://www.postgresql.org/docs/16/functions-sequence.html)

Textbook background for the viva (database security chapters): [Elmasri & Navathe ch. 30 slides](https://www.cs.purdue.edu/homes/bb/cs448_Fall2017/lpdf/Chapter30.pdf), [Ramakrishnan & Gehrke ch. 17 slides](https://pages.cs.wisc.edu/~dbbook/openAccess/secondEdition/slides/slides2ed-english/Chapter17.pdf), [OWASP Top 10:2025](https://owasp.org/Top10/2025/0x00_2025-Introduction/).
