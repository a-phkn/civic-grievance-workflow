# Civic Grievance Workflow System: Phase 1

NLP-Assisted Multi-Agency Civic Grievance Workflow Management System (CD252IA, DBMS).
Phase 1 delivers the database, a working web front end for all four roles, role-wise
access, server-side validation and security, and tests that prove each of them.

The design (ER diagram, relational schema, DFDs, normalization) is frozen. Every table,
rule and screen here traces back to it; `db/README.md` has the table-by-table mapping.

## What works in this phase

| Role | Can do | DFD |
| --- | --- | --- |
| Citizen | Register, log in, report a problem with an optional photo, follow each step of their own complaints, see what a waiting step waits for and why a complaint was rejected | 1.0, 2.0, 6.0 |
| Agency staff | See only their agency's ready tasks, start them, mark them completed or failed | 5.1, 5.2 |
| (database) | When every prerequisite of a task is completed, the task unlocks with a new deadline; if one fails, it goes on hold for review | 5.3 |
| Reviewer | See all complaints and their history, reject with a reason, retry / cancel failed tasks, waive a dependency, view reports | 4.3, 5.4, 6.0 |
| Admin | Add agencies, task types with SLAs, change an SLA, create staff and reviewer accounts | 1.0 |

Not in this phase: the NLP step (DFD 3.0) that reads a complaint and proposes tasks.
Complaint 101 in the demo data stands in for an already-accepted proposal, so the whole
task workflow can be shown.

## Tech stack (and why)

| Part | Choice | Why this and not the alternative |
| --- | --- | --- |
| Web | Flask 3 + Jinja templates | Small and explicit; every route is readable in a viva. Django's ORM cannot model our composite foreign keys (TASK -> AGENCY_TASK_TYPE, TASK_DEPENDENCY -> TASK). |
| Main database | PostgreSQL 16 | Triggers may update the same table (MySQL error 1442 blocks this), `SELECT ... FOR UPDATE` for the concurrent unlock, real CHECKs. SQLite has one writer and no row locks. |
| SQL access | psycopg 3, plain parameterised SQL | The SQL in the code is the SQL in the report. Parameters are never pasted into the query text, so no SQL injection. |
| Document store | MongoDB 8 via pymongo | The DFD names D8 complaints_raw and D9 ai_proposals as MongoDB; AI proposals are nested and vary in shape, and unvalidated AI output stays out of the relational data. |
| Login | Flask-Login + our own `role_required` | Flask-Login only tracks who is logged in; the role is read from APP_USER on every request. |
| Forms | Flask-WTF / WTForms | Server-side validation and CSRF tokens on every form, built in. |
| Passwords | Argon2id (argon2-cffi defaults: 64 MiB, 3 passes, 4 lanes) | OWASP's first choice. Werkzeug's default scrypt (n = 2^15) is below OWASP's 2^17. |

The cost of two databases, and how it is handled, is in the Reconciled Design doc
("Complaint submission across both databases") and tested by
`test_mongodb_failure_rolls_back_postgres`.

## Folder layout

```
app/            Flask app: one blueprint per role (citizen, staff, reviewer, admin) + auth
  forms.py      every form and its validation rules
  templates/    pages; _form.html draws every field the same way
  static/       style.css, app.js (counter, small helpers; no rule depends on JS)
db/             schema.sql, seed_demo.sql, roles.sql, mongo_users.py, seed_mongo.py, test_schema.py, README.md
tests/          test_app.py: 41 end-to-end tests of the app
docs/           SECURITY_AND_VALIDATION.md, FORM_DESIGN_VIVA.md, screenshots/, test-results/
run.py          starts the app on http://127.0.0.1:5000
```

## Run it on a Mac

You need Homebrew (https://brew.sh) and Python 3.11 or newer.

### 1. Install and start the two databases (once)

```bash
brew install postgresql@16
brew services start postgresql@16
echo 'export PATH="$(brew --prefix postgresql@16)/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

brew tap mongodb/brew
brew trust mongodb/brew          # newer Homebrew asks you to trust non-core taps (MongoDB's official tap)
brew install mongodb-community@8.0
brew services start mongodb-community@8.0
```

Homebrew's PostgreSQL makes your Mac user a superuser with no password, so `psql`
works without `-U`.

### 2. Create the database and load the demo data

From the project folder:

```bash
createdb grievance
psql -d grievance -v ON_ERROR_STOP=1 -f db/schema.sql
psql -d grievance -v ON_ERROR_STOP=1 -f db/seed_demo.sql
psql -d grievance -v ON_ERROR_STOP=1 -v app_password="'app-password'" -f db/roles.sql
```

`roles.sql` creates `grievance_app`, the login the web app uses. It cannot change the
schema, edit or delete audit entries, or delete complaints. Run it again whenever you
re-run `schema.sql`.

### 3. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Turn on MongoDB logins (once), then load its demo data

```bash
MONGO_ADMIN_PASSWORD='choose-an-admin-password' python db/mongo_users.py
printf 'security:\n  authorization: enabled\n' >> "$(brew --prefix)/etc/mongod.conf"
brew services restart mongodb-community@8.0
python db/seed_mongo.py          # MongoDB demo documents for complaint 101
```

`mongo_users.py` creates `grievance_app` (password `app-password`, read and write on the
`grievance` database only) and `grievance_admin` (can only manage users). After the
restart MongoDB refuses anyone without a login. Run `mongo_users.py` only once, before
the restart; afterwards it would need the admin login.

### 5. Start the app

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python run.py
```

Open http://127.0.0.1:5000. The defaults in `app/config.py` already point at
`grievance_app:app-password@localhost/grievance` for both databases; set
`DATABASE_URL`, `MONGO_URL` or `UPLOAD_FOLDER` only if yours differ.

### Demo accounts (password `Demo@2026` for all)

| Email | Role |
| --- | --- |
| citizen@civic.demo | Citizen (Priya), owns complaint 101 |
| water.staff@civic.demo | Agency staff, Water (task 1) |
| power.staff@civic.demo | Agency staff, Electricity (task 2) |
| roads.staff@civic.demo | Agency staff, Roads (task 3, waits for 1 and 2) |
| reviewer@civic.demo | Reviewer (Ravi) |
| admin@civic.demo | Admin |

### A 5-minute demo

1. Log in as **water.staff**: start task 1, then mark it completed.
2. Log in as **roads.staff**: nothing yet. Task 3 still waits for task 2.
3. Log in as **power.staff**: start task 2, then mark it completed.
4. Log in as **roads.staff** again: task 3 has appeared with a fresh deadline (the unlock trigger, DFD 5.3).
5. Log in as **citizen**: complaint 101 shows each step done.
6. Log in as **reviewer**: open complaint 101 to see the full audit history; open Reports.

For the exception path: have a staff member report a failure, then as the reviewer
open Exceptions and retry or cancel the task (a reason is required).

## Run the tests

Both suites rebuild the databases from scratch, so run them on a demo database only.

```bash
# database rules: 49 tests
DATABASE_URL=postgresql://localhost/grievance python db/test_schema.py

# web app: 41 tests (needs MongoDB running)
OWNER_DATABASE_URL=postgresql://localhost/grievance python -m pytest -v tests/test_app.py
```

Run the app tests last, or re-run `roles.sql` (step 2) afterwards: the database tests
set a different password on `grievance_app`. The results from our own run are in
`docs/test-results/`.

## Documents for the evaluation

- `docs/SECURITY_AND_VALIDATION.md`: each security and validation measure, where it is
  in the code, the test that proves it, and the source it follows.
- `docs/FORM_DESIGN_VIVA.md`: how the forms were designed (simplicity, field types, user
  guidance, layout) with likely viva questions and answers.
- `docs/screenshots/`: 24 screenshots of every role, including validation errors, a
  blocked role and a rejected forged request.
- `db/README.md`: traceability to the ER diagram and relational schema, the allowed
  task status changes, and the decisions the design documents left open.
