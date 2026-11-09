# ClassBuddy - Smart Class Attendance and Reminder System

ClassBuddy is a Flask application for managing personal class timetables,
recording attendance, viewing attendance-risk predictions, and receiving
automatic email reminders 15 minutes before each class by default.

## Features

- Student registration and login with hashed passwords
- SMTP email delivery using credentials stored in the environment
- Student-owned weekly timetable manager (add, edit, and delete classes)
- Attendance logger and analytics
- Browser notifications before class
- Automatic email reminders to each student's registered email address
- SQLite-backed reminder history to prevent duplicate emails
- No-store cache headers for authenticated pages

## Existing timetable migration

On startup, ClassBuddy adds a nullable `student_id` foreign-key column and an
index to the existing `schedule` table if they are missing. Existing timetable
rows and their IDs are preserved with `student_id` set to `NULL`; because the
old database did not record ownership, the application does not guess which
student owns them. Unassigned rows are hidden from student pages and are not
used for reminders. New timetable rows are always stored with the currently
authenticated student's numeric `students.id`.

Before making a legacy row visible, identify its owner and assign it using its
row ID and the correct numeric student ID. For example, after backing up
`database/classbuddy.db`, use a SQLite client to run:

```sql
-- Example only: replace these numeric IDs with your verified mapping.
UPDATE schedule SET student_id = 1 WHERE id IN (1, 2);
```

Do not assign all existing rows to one student unless the actual ownership
mapping confirms that is correct. The database currently has 24 legacy
timetable rows without ownership information; the correct owner of each row
or group must be identified before they can be assigned.

## Install and configure (Windows PowerShell)

From the `ClassBuddy` project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `SECRET_KEY` and the SMTP settings: `MAIL_SERVER`,
`MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`, and
`MAIL_DEFAULT_SENDER`. Use the host, port, and TLS mode specified by your
email provider (587 with STARTTLS is a common configuration). Optionally set
`MAIL_SENDER_NAME` and `MAIL_TIMEOUT`. Keep the SMTP credentials only in `.env`
or the deployment secret store; they are never included in browser code or
application diagnostics. Students register and log in with a ClassBuddy
website password, which is not used for email delivery.
`.env` is excluded by `.gitignore` and loaded from the project directory
regardless of the current working directory.

## Run the application

For local development, `python app.py` starts the web app and its reminder
scheduler. In debug/reloader mode the scheduler runs only in the reloader's
serving process. WSGI workers do not start schedulers by default; the
standalone `run_scheduler.py` process takes a per-database operating-system
lock so a second scheduler in any app process cannot run concurrently. The
scheduler checks every 30 seconds and sends at the configurable
each student's saved reminder offset (15 minutes by default).
Checks are aligned to the wall clock; after downtime, reminders can be sent
late as long as class has not started. The process must remain running; no
student needs to visit the website.

Open `http://127.0.0.1:5000` and register or sign in with a student email and
password. Automatic reminders are sent through the configured SMTP server to
each student's registered email address. The Settings page can enable or disable
reminders and set an individual offset of 5, 10, 15, 20, or 30 minutes.
Changes apply on the next scheduler poll and are retained across logouts.
Reminder messages include responsive HTML and plain-text content. Provider
failures are logged without API credentials or provider response bodies.

## Deployment and multiple workers

A scheduler embedded in a Flask process stops when that process stops. Keep
the app and scheduler alive with a process manager (for example, Windows
Service Manager/NSSM, systemd, or a container restart policy).

For a multi-worker web deployment, run exactly one separate scheduler process:

1. Set `REMINDER_SCHEDULER_ENABLED=False` in the environment used by all web
   workers.
2. Start one long-lived scheduler process from this project directory:

   ```powershell
   .\.venv\Scripts\python.exe run_scheduler.py
   ```

3. Configure the process manager to restart `run_scheduler.py` if it exits.

The scheduler and web workers must use the same SQLite database file.
`email_reminder_history` claims each student/class occurrence transactionally
and has a unique key, so another worker cannot claim an occurrence already
being sent. A failed or interrupted send is recorded rather than retried
automatically, avoiding duplicate delivery if the mail server accepted a
message but the connection failed before confirmation. This is an at-most-once
policy: an abrupt process failure after claiming but before sending may mean
that occurrence is missed.

## SQLite location and concurrency

Database connections use a 15-second SQLite busy timeout, enable foreign-key
checks, and are closed after each operation. Writes are committed on success
and rolled back on failure. The scheduler and web requests use independent
connections; connections are never shared between threads.

Startup serializes schema initialization across ClassBuddy threads and
processes, and checks for missing schema before opening a write transaction.
Normal restarts do not request an SQLite write lock. Schema changes are applied
in a single bounded transaction; startup reports a lock error with the
database path and OneDrive guidance if another process or the sync client
continues to block that migration.

By default, the active database is stored outside the project under the
operating system's local application-data directory (on Windows,
`%LOCALAPPDATA%\ClassBuddy\classbuddy.db`). On first startup, if the legacy
`database/classbuddy.db` exists and the new local database does not, ClassBuddy
uses SQLite's backup API to copy it. The original database is retained. Stop
all ClassBuddy processes before upgrading so the old OneDrive copy is not
written while it is being migrated. Do not delete the original until the new
installation is verified and backed up. After migration, the local
`%LOCALAPPDATA%\ClassBuddy\classbuddy.db` is the active database; the project
copy is not synchronized with it. ClassBuddy logs the active database path at
startup. To use a different existing database, set `CLASSBUDDY_DATABASE`
explicitly and stop all ClassBuddy processes before changing it.

Do not use SQLite WAL mode while the active database is in a
OneDrive-synchronized folder: WAL depends on sidecar files that a sync client
may copy or reconcile while SQLite is using them. You can explicitly choose a
different database location with `CLASSBUDDY_DATABASE`; keep that location
outside synchronized folders for reliable multi-user use.

After moving the database to local, non-synchronized storage, WAL can be
enabled by setting `CLASSBUDDY_SQLITE_WAL=True`. Startup rejects this setting
when the configured path is under OneDrive. Leave it unset/false in a synced
directory.

## Database and timetable behavior

The application creates `email_reminder_history` and
`student_notification_settings`, and migrates the schedule ownership column
automatically when it opens the existing SQLite database. Existing students
receive an enabled reminder preference with a 15-minute default, without
changing their student or timetable records. The settings table stores each
student's enabled state, reminder offset, and update timestamp.
Existing students, attendance, predictions, and timetable rows are preserved.
The scheduler reloads each student's timetable every poll, so class updates
and deletions are reflected without stale per-class jobs. Class occurrences
are interpreted in `Asia/Kolkata`; classes shortly after midnight are matched
to the next calendar day.

Login replaces old session contents, logout clears the session and expires the
remember cookie, and authenticated responses use `Cache-Control: no-store`.

## Tests

Automated tests mock SMTP connections and do not send real messages:

```powershell
python -m unittest discover -s tests -v
```

To verify a real send, configure SMTP credentials in `.env`, start ClassBuddy,
sign in, and use **Send Test Email** from Settings. Confirm receipt in the
recipient inbox. Real delivery has not been verified by the automated tests.

The existing database diagnostic can be run with:

```powershell
python test_db.py
```

Train the attendance model once before using risk predictions:

```powershell
python ml/train_model.py
```
