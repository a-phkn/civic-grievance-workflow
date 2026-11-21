# Security and validation

Rubric row: *Front-end design with a user-friendly GUI and **security validation***.
Every measure below says what it stops, where it is in the code, which test proves it,
and the source it follows. Test names are in `tests/test_app.py` (app) or
`db/test_schema.py` (database). Results: `docs/test-results/`.

Principle: **the server decides, the database double-checks.** Nothing in the browser
(hidden fields, disabled buttons, JavaScript) is trusted. Forms use `novalidate`, so
every message the user sees comes from the server, and a request crafted by hand meets
the same rules as one from the page.

## 1. Role-wise access

| Measure | Stops | Where | Proved by |
| --- | --- | --- | --- |
| `@role_required(...)` on every protected route; role read from APP_USER on each request | A citizen opening staff, reviewer or admin pages | `app/auth.py` `role_required`; every route in `citizen.py`, `staff.py`, `reviewer.py`, `admin.py` | `test_each_role_sees_only_its_own_pages` (all 4 roles x 4 areas), screenshot 08 |
| Denied attempts are written to AUDIT_LOG as `ACCESS_DENIED` | Silent probing | `role_required`, `staff.update_status` | same test checks the audit row |
| Not logged in -> login page | Anonymous access | Flask-Login `login_required` inside `role_required` | `test_anonymous_is_sent_to_login` |
| Citizen sees only own complaints; someone else's id gives the same 404 as a missing one | Reading other citizens' complaints, guessing ids | `citizen.complaint_detail` (`WHERE citizen_id = me`) | `test_citizen_cannot_open_another_citizens_complaint` |
| Staff agency comes from APP_USER, never from the form or URL; another agency's task -> 403 + audit | Staff changing another agency's task (DFD 5.1, 5.2) | `staff.task_list`, `staff.update_status` | `test_staff_cannot_update_another_agencys_task` |
| Registration always creates CITIZEN; a `role` field sent by hand is ignored | Self-promotion to admin | `auth.register` (role is a constant in the SQL) | `test_register_cannot_choose_a_role` |
| Photos stored outside `/static`, served only to the owner or a reviewer, and only if MongoDB lists that file for that complaint | Reading photos by guessing URLs | `citizen.photo` | `test_photo_upload_accepted_and_private` |
| Database login `grievance_app` has only the rights it needs: no UPDATE/DELETE on audit_log, no DELETE on complaints, no schema changes | A bug or injection in the app wiping history | `db/roles.sql` | `db/test_schema.py` app-role tests |
| MongoDB runs with authorization on; the app logs in as `grievance_app` with readWrite on the `grievance` database only (cannot read other databases or create users) | Anyone on the machine reading or changing complaint text and AI proposals | `db/mongo_users.py`, `config.MONGO_URL` | `test_mongodb_needs_a_login_and_app_user_is_limited` |

Source: OWASP Authorization Cheat Sheet (deny by default, check on every request,
check ownership); PostgreSQL `GRANT` docs; MongoDB manual "Enable Access Control" and built-in role `readWrite`.

## 2. Authentication

| Measure | Stops | Where | Proved by |
| --- | --- | --- | --- |
| Passwords hashed with **Argon2id** (64 MiB, 3 passes, 4 lanes; library defaults, RFC 9106 low-memory profile) | Cracking leaked hashes | `auth.py` `PasswordHasher()`; demo hashes in `seed_demo.sql` | `test_passwords_are_argon2id_and_never_plain` |
| Hash upgraded after login if parameters change | Old weak hashes staying forever | `ph.check_needs_rehash` in `auth.login` | (library behaviour) |
| 5 wrong passwords in 15 minutes lock the account for 15 minutes (counted from AUDIT_LOG) | Password guessing | `auth._recent_failures`; limits in `config.py` | `test_login_locks_after_five_failures` |
| Same message for unknown email and wrong password; unknown email still runs one Argon2 check | Finding out which emails are registered (by message or by response time) | `auth.login` `_DUMMY_HASH` | `test_unknown_email_and_wrong_password_look_the_same` |
| Password rules: 8-128 characters, at least one letter and one number; confirm field must match | Trivial passwords | `forms.strong_password`, `RegisterForm` | `test_register_validation_messages`, screenshot 02 |
| Session cleared at login; cookie HttpOnly, SameSite=Lax, Secure when `SESSION_COOKIE_SECURE=1`; ends when the browser closes; form tokens expire after 30 minutes; Flask-Login `session_protection="strong"` | Session fixation, cookie theft by script | `auth.login`, `config.py` | `test_security_headers` (cookie flags) |
| After login, always go to the role's home page; a `?next=` URL is never followed | Open redirect to a fake site | `auth.login` | `test_next_parameter_is_never_followed` |
| Logout only by POST with CSRF token | Another site logging you out with a link | `auth.logout` | `test_logout_requires_post` |

Source: OWASP Password Storage Cheat Sheet (Argon2id first; min 19 MiB / 2 passes);
OWASP Authentication Cheat Sheet (generic errors, lockout); Flask "Security
Considerations" (cookie flags).

## 3. Attacks through input

| Measure | Stops | Where | Proved by |
| --- | --- | --- | --- |
| CSRF token on every form (Flask-WTF `CSRFProtect`); missing or wrong token -> 400 "Form expired or invalid" | Another site submitting forms as you | `app/__init__.py`; `form.hidden_tag()` in every template | `test_post_without_csrf_token_is_rejected`, screenshot 21 |
| All SQL uses `%s` parameters (psycopg sends values separately from the query) | SQL injection | every `conn.execute` | `test_sql_injection_in_login_fails`, `test_sql_injection_text_is_stored_as_plain_text` |
| Jinja auto-escaping; no `|safe` anywhere | Stored XSS (script in a complaint) | all templates | `test_script_in_complaint_is_escaped` |
| Content-Security-Policy `default-src 'self'` with no inline scripts or styles | XSS even if escaping were missed | `app/__init__.py` `security_headers` | `test_security_headers` |
| `X-Frame-Options: DENY`, `frame-ancestors 'none'` | Clickjacking | same | same |
| `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cache-Control: no-store` when logged in | Content sniffing, URL leaks, private pages left in the browser cache | same | same |
| Debug mode off in `run.py` | The Werkzeug debugger letting anyone run code | `run.py` | (read the file) |

Source: Flask-WTF CSRF docs; psycopg 3 "Passing parameters to SQL queries"; OWASP XSS
Prevention Cheat Sheet; Flask "Security Considerations" (headers).

## 4. File upload (complaint photo)

| Check | Stops | Where | Proved by |
| --- | --- | --- | --- |
| Extension must be jpg / jpeg / png | Scripts and other files | `ComplaintForm.photo` `FileAllowed`; `citizen._save_photo` | `test_wrong_extension_is_rejected` |
| First bytes must match JPEG or PNG ("magic bytes") | A renamed `.exe` or `.html` | `citizen.SIGNATURES`, `_save_photo` | `test_renamed_non_image_is_rejected` |
| 5 MB limit for the whole request (`MAX_CONTENT_LENGTH`) -> 413 page | Filling the disk / memory | `config.py`, 413 handler | `test_upload_over_5mb_is_refused` |
| File saved under a random name (`uuid4`), never the user's file name | Path tricks (`../../`), overwriting, guessable URLs | `_save_photo` | `test_photo_upload_accepted_and_private` |
| Stored outside `/static`; served by a checked route; `nosniff` header | Direct download by anyone | `config.UPLOAD_FOLDER`, `citizen.photo` | same |

Source: OWASP File Upload Cheat Sheet (allow-list, signature check, rename, size
limit, store outside web root).

## 5. Validation: form rule = database rule

Each form rule mirrors a rule already in the frozen normalization domains or the
schema, so the user gets a clear message first, and the database still refuses bad data
if the app is bypassed (tested in `db/test_schema.py`).

| Field | Form rule (`app/forms.py`) | Database rule (`db/schema.sql`) |
| --- | --- | --- |
| Email | required, valid format, max 255, stored lower case | `VARCHAR(255) UNIQUE`, CHECK `email = lower(email)` |
| Name | 2-100, letters, spaces, `. ' -` | `VARCHAR(100) NOT NULL` |
| Complaint description | 20-2,000 characters, trimmed | `NOT NULL`, CHECK not blank |
| Task status (staff) | select offering only the legal next steps from the current status | trigger `fn_task_status_rules`: only the 11 allowed transitions |
| Reviewer reason | 5-500 characters | `fn_require_reason` (5+ characters) |
| Task code | 2-30, `^[A-Z][A-Z0-9_]*$`, upper-cased | part of PK `(agency_id, task_code)` |
| SLA hours | whole number 1-8,760 | CHECK `sla_hours > 0` |
| Staff account agency | required for agency staff, must be empty for a reviewer | CHECK `chk_user_agency` |
| Duplicate email / agency | friendly message on `UniqueViolation` | UNIQUE constraints |

Proved by: `test_register_validation_messages`, `test_duplicate_email_message`,
`test_complaint_too_short`, `test_admin_sla_must_be_positive`,
`test_admin_staff_needs_agency`, `test_staff_cannot_skip_a_step` (a hand-made request for
READY -> COMPLETED is refused), screenshots 02, 03, 15, 20.

## 6. Integrity and audit

| Measure | Where | Proved by |
| --- | --- | --- |
| A complaint is kept in both PostgreSQL and MongoDB, or in neither: the PostgreSQL transaction stays open until MongoDB has the document; on failure the Mongo doc and photo are removed and the user sees "could not be saved" | `citizen.new_complaint` | `test_complaint_written_to_postgres_and_mongodb`, `test_mongodb_failure_rolls_back_postgres` |
| The MongoDB copy holds no name or email (only text, photo names, ids), ready for the NLP step | `citizen.new_complaint` | `test_complaint_written_to_postgres_and_mongodb` |
| Concurrent status change: the UPDATE applies only if the status is still what the user saw | `staff.update_status` (`WHERE status = old`) | (no automated test; the UPDATE's row count decides the message) |
| Two prerequisites completing at the same moment still unlock the dependent once (`FOR UPDATE`) | trigger `fn_task_unlock` | `db/test_schema.py` concurrent unlock test |
| Every status change, login, failed login, denied access, reviewer decision and admin change is written to AUDIT_LOG; the app cannot edit or delete it | `db.audit`, triggers, `roles.sql` | `test_admin_sla_change_is_audited`, role tests |
| Secrets come from environment variables, not the code; with no `SECRET_KEY` set, a random one is made at start-up, never a fixed default | `config.py` | (read the file) |

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
- Registration has no CAPTCHA or email verification in this phase.
