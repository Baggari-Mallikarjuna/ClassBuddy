import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from flask import Flask
from models.student import Student
from models.schedule import Schedule
from services.reminder_scheduler import (
    SCHEDULER_EXTENSION_KEY,
    send_due_reminders,
    start_reminder_scheduler,
)
from utils import database
from utils.process_lock import acquire_process_lock


class DatabaseConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / 'classbuddy.db')
        self.database_patch = patch.object(
            database,
            'DATABASE_PATH',
            self.database_path
        )
        self.database_patch.start()
        wal_patch = patch.dict(
            os.environ,
            {'CLASSBUDDY_SQLITE_WAL': 'false'}
        )
        wal_patch.start()
        self.addCleanup(wal_patch.stop)
        database.init_db()

    def tearDown(self):
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def test_connection_has_busy_timeout_and_foreign_keys(self):
        with database.managed_connection() as conn:
            busy_timeout = conn.execute(
                'PRAGMA busy_timeout'
            ).fetchone()[0]
            foreign_keys = conn.execute(
                'PRAGMA foreign_keys'
            ).fetchone()[0]

        self.assertEqual(busy_timeout, database.SQLITE_BUSY_TIMEOUT_MS)
        self.assertEqual(foreign_keys, 1)

    def test_failed_transaction_rolls_back(self):
        with self.assertRaisesRegex(RuntimeError, 'forced failure'):
            with database.managed_connection() as conn:
                conn.execute(
                    'INSERT INTO students (name, email, password) '
                    'VALUES (?, ?, ?)',
                    ('Rollback Student', 'rollback@example.com', 'hash')
                )
                raise RuntimeError('forced failure')

        with database.managed_connection() as conn:
            count = conn.execute(
                'SELECT COUNT(*) FROM students WHERE email = ?',
                ('rollback@example.com',)
            ).fetchone()[0]

        self.assertEqual(count, 0)

    def test_existing_schema_initialization_does_not_request_write_lock(self):
        writer = database.get_db_connection()
        writer.execute('BEGIN IMMEDIATE')
        writer.execute(
            'INSERT INTO students (name, email, password) '
            'VALUES (?, ?, ?)',
            ('Uncommitted Writer', 'uncommitted@example.com', 'hash')
        )
        try:
            database.init_db()
        finally:
            writer.rollback()
            writer.close()

        with database.managed_connection() as conn:
            row = conn.execute(
                'SELECT id FROM students WHERE email = ?',
                ('uncommitted@example.com',)
            ).fetchone()
        self.assertIsNone(row)

    def test_concurrent_first_time_initialization_is_serialized(self):
        fresh_path = str(Path(self.temp_dir.name) / 'fresh' / 'classbuddy.db')
        with patch.object(database, 'DATABASE_PATH', fresh_path):
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [
                    executor.submit(database.init_db)
                    for _ in range(8)
                ]
                results = [
                    future.result(timeout=30)
                    for future in futures
                ]

            self.assertEqual(results, [None] * 8)
            with database.managed_connection() as conn:
                tables = {
                    row['name']
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            self.assertTrue({
                'students',
                'schedule',
                'attendance',
                'predictions',
                'email_reminder_history',
            }.issubset(tables))

    def test_repeated_initialization_preserves_student_timetable_and_history(self):
        student_id = Student.create(
            'Persistent Student',
            'persistent@example.com',
            'test-password'
        )
        schedule_id = Schedule.add(
            student_id,
            'Persistent Class',
            'Faculty',
            'Room 1',
            'Monday',
            '10:00',
            '11:00'
        )
        with database.managed_connection() as conn:
            conn.execute(
                '''INSERT INTO email_reminder_history
                   (student_id, schedule_id, class_start, status, attempted_at)
                   VALUES (?, ?, ?, ?, ?)''',
                (
                    student_id,
                    schedule_id,
                    '2026-11-02T10:00+05:30',
                    'sent',
                    '2026-11-02T09:55:00+05:30'
                )
            )

        for _ in range(3):
            database.init_db()

        with database.managed_connection() as conn:
            student = conn.execute(
                'SELECT id, email FROM students WHERE id = ?',
                (student_id,)
            ).fetchone()
            schedule = conn.execute(
                'SELECT id, subject FROM schedule WHERE id = ?',
                (schedule_id,)
            ).fetchone()
            history = conn.execute(
                'SELECT status FROM email_reminder_history '
                'WHERE student_id = ? AND schedule_id = ?',
                (student_id, schedule_id)
            ).fetchone()

        self.assertEqual(student['email'], 'persistent@example.com')
        self.assertEqual(schedule['subject'], 'Persistent Class')
        self.assertEqual(history['status'], 'sent')

    def test_student_and_timetable_writes_survive_process_restart(self):
        student_id = Student.create(
            'Restart Student',
            'restart@example.com',
            'test-password'
        )
        self.assertIsNotNone(student_id)

        updated_schedule_id = Schedule.add(
            student_id,
            'Original Class',
            'Faculty',
            'Room 1',
            'Monday',
            '10:00',
            '11:00'
        )
        deleted_schedule_id = Schedule.add(
            student_id,
            'Class To Delete',
            'Faculty',
            'Room 2',
            'Monday',
            '11:00',
            '12:00'
        )
        self.assertTrue(Schedule.update(
            student_id,
            updated_schedule_id,
            'Updated Class',
            'Faculty',
            'Room 1',
            'Monday',
            '10:00',
            '11:00'
        ))
        self.assertTrue(Schedule.delete(student_id, deleted_schedule_id))

        restart_check = subprocess.run(
            [
                sys.executable,
                '-c',
                '''
import json
from utils.database import init_db, managed_connection

init_db()
with managed_connection() as conn:
    student = conn.execute(
        'SELECT id FROM students WHERE email = ?',
        ('restart@example.com',)
    ).fetchone()
    classes = conn.execute(
        'SELECT subject FROM schedule WHERE student_id = ? ORDER BY subject',
        (student['id'],)
    ).fetchall() if student else []
print(json.dumps({
    'student_exists': student is not None,
    'classes': [row['subject'] for row in classes],
}))
'''
            ],
            cwd=database.BASE_DIR,
            env={
                **os.environ,
                'CLASSBUDDY_DATABASE': self.database_path,
            },
            check=True,
            capture_output=True,
            text=True,
            timeout=30
        )

        self.assertEqual(
            restart_check.stdout.strip(),
            '{"student_exists": true, "classes": ["Updated Class"]}'
        )

    def test_notification_settings_migration_preserves_existing_data(self):
        legacy_path = str(
            Path(self.temp_dir.name) / 'legacy-settings.db'
        )
        conn = sqlite3.connect(legacy_path)
        conn.executescript('''
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            );
            CREATE TABLE schedule (
                id INTEGER PRIMARY KEY,
                subject TEXT NOT NULL,
                faculty TEXT NOT NULL,
                room TEXT NOT NULL,
                day TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                student_id INTEGER REFERENCES students (id)
            );
            CREATE TABLE student_notification_settings (
                student_id INTEGER PRIMARY KEY REFERENCES students (id),
                email_reminders_enabled INTEGER NOT NULL DEFAULT 1
                    CHECK (email_reminders_enabled IN (0, 1))
            );
            INSERT INTO students VALUES
                (1, 'Existing One', 'one@example.com', 'hash'),
                (2, 'Existing Two', 'two@example.com', 'hash');
            INSERT INTO schedule VALUES
                (1, 'Existing Class', 'Faculty', 'Room 1', 'Monday',
                 '10:00', '11:00', 1);
            INSERT INTO student_notification_settings
                (student_id, email_reminders_enabled) VALUES (1, 0);
        ''')
        conn.commit()
        conn.close()

        with patch.object(database, 'DATABASE_PATH', legacy_path):
            database.init_db()
            with database.managed_connection() as migrated:
                student_settings = migrated.execute(
                    '''SELECT student_id, email_reminders_enabled,
                              reminder_minutes, updated_at
                       FROM student_notification_settings ORDER BY student_id'''
                ).fetchall()
                schedule = migrated.execute(
                    'SELECT subject, student_id FROM schedule WHERE id = 1'
                ).fetchone()

        self.assertEqual(len(student_settings), 2)
        self.assertEqual(student_settings[0]['email_reminders_enabled'], 0)
        self.assertEqual(student_settings[0]['reminder_minutes'], 15)
        self.assertTrue(student_settings[0]['updated_at'])
        self.assertEqual(student_settings[1]['email_reminders_enabled'], 1)
        self.assertEqual(student_settings[1]['reminder_minutes'], 15)
        self.assertEqual(schedule['subject'], 'Existing Class')
        self.assertEqual(schedule['student_id'], 1)

    def test_existing_database_is_backed_up_to_local_default_and_preserved(self):
        source_path = str(
            Path(self.temp_dir.name) / 'legacy' / 'classbuddy.db'
        )
        target_path = str(
            Path(self.temp_dir.name) / 'local' / 'classbuddy.db'
        )
        Path(source_path).parent.mkdir(parents=True)
        source = sqlite3.connect(source_path)
        try:
            source.execute(
                '''CREATE TABLE students (
                       id INTEGER PRIMARY KEY,
                       name TEXT NOT NULL,
                       email TEXT UNIQUE NOT NULL,
                       password TEXT NOT NULL
                   )'''
            )
            source.execute(
                'INSERT INTO students VALUES (?, ?, ?, ?)',
                (7, 'Existing Student', 'existing@example.com', 'stored-hash')
            )
            source.commit()
        finally:
            source.close()

        with patch.object(database, 'DATABASE_PATH', target_path):
            with patch.object(database, 'DEFAULT_DATABASE_PATH', target_path):
                with patch.object(
                    database,
                    'LEGACY_DATABASE_PATH',
                    source_path
                ):
                    database.init_db()
                    with database.managed_connection() as conn:
                        student = conn.execute(
                            'SELECT id, name, email, password FROM students '
                            'WHERE id = ?',
                            (7,)
                        ).fetchone()
                        tables = {
                            row['name']
                            for row in conn.execute(
                                "SELECT name FROM sqlite_master "
                                "WHERE type = 'table'"
                            ).fetchall()
                        }

        self.assertEqual(
            tuple(student),
            (7, 'Existing Student', 'existing@example.com', 'stored-hash')
        )
        self.assertIn('email_reminder_history', tables)
        self.assertIn('student_notification_settings', tables)
        self.assertTrue(os.path.exists(source_path))

    def test_concurrent_student_writes_use_independent_connections(self):
        student_count = 24

        def create_student(index):
            return Student.create(
                f'Student {index}',
                f'student-{index}@example.com',
                'test-password'
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            student_ids = list(executor.map(
                create_student,
                range(student_count)
            ))

        self.assertTrue(all(student_ids))
        self.assertEqual(len(set(student_ids)), student_count)
        with database.managed_connection() as conn:
            count = conn.execute(
                'SELECT COUNT(*) FROM students'
            ).fetchone()[0]
        self.assertEqual(count, student_count)

    def test_registration_waits_for_a_short_sqlite_write_lock(self):
        lock_acquired = threading.Event()
        release_lock = threading.Event()

        def hold_write_lock():
            conn = sqlite3.connect(
                self.database_path,
                timeout=database.SQLITE_TIMEOUT_SECONDS
            )
            try:
                conn.execute('BEGIN IMMEDIATE')
                lock_acquired.set()
                release_lock.wait(timeout=5)
                conn.rollback()
            finally:
                conn.close()

        lock_thread = threading.Thread(target=hold_write_lock)
        lock_thread.start()
        release_timer = None
        student_id = None
        try:
            self.assertTrue(lock_acquired.wait(timeout=5))
            release_timer = threading.Timer(0.2, release_lock.set)
            release_timer.start()
            student_id = Student.create(
                'Lock Contention Student',
                'lock-contention@example.com',
                'test-password'
            )
        finally:
            release_lock.set()
            if release_timer is not None:
                release_timer.cancel()
                release_timer.join(timeout=5)
            lock_thread.join(timeout=5)

        self.assertFalse(lock_thread.is_alive())
        self.assertIsNotNone(student_id)

    def test_reminder_scan_runs_during_student_registrations(self):
        timezone = ZoneInfo('Asia/Kolkata')
        now = datetime(2026, 11, 2, 9, 55, tzinfo=timezone)
        student_id = Student.create(
            'Reminder Student',
            'reminder@example.com',
            'test-password'
        )
        Schedule.add(
            student_id,
            'Concurrent Class',
            'Faculty',
            'Room 1',
            'Monday',
            '10:00',
            '11:00'
        )
        app = Flask(__name__)
        app.config.update({
            'REMINDER_TIMEZONE': 'Asia/Kolkata',
            'REMINDER_MINUTES_BEFORE': 15,
            'MAIL_SERVER': 'smtp.example.com',
            'MAIL_PORT': 587,
            'MAIL_USE_TLS': True,
            'MAIL_USERNAME': 'smtp-user',
            'MAIL_PASSWORD': 'test-only-smtp-password',
            'MAIL_DEFAULT_SENDER': 'sender@example.com',
        })

        def create_students():
            return [
                Student.create(
                    f'Concurrent Student {index}',
                    f'concurrent-{index}@example.com',
                    'test-password'
                )
                for index in range(12)
            ]

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            with ThreadPoolExecutor(max_workers=2) as executor:
                reminder_scan = executor.submit(
                    send_due_reminders,
                    app,
                    now
                )
                registrations = executor.submit(create_students)
                reminder_scan.result(timeout=30)
                student_ids = registrations.result(timeout=30)

        self.assertTrue(all(student_ids))
        send_message.assert_called_once()

    def test_wal_is_rejected_for_onedrive_path(self):
        onedrive_path = str(
            Path(self.temp_dir.name) / 'OneDrive' / 'classbuddy.db'
        )
        with patch.object(database, 'DATABASE_PATH', onedrive_path):
            with patch.dict(
                os.environ,
                {'CLASSBUDDY_SQLITE_WAL': 'true'}
            ):
                with self.assertRaisesRegex(RuntimeError, 'OneDrive'):
                    database.init_db()

    def test_existing_wal_mode_is_rejected_for_onedrive_path(self):
        onedrive_path = str(
            Path(self.temp_dir.name) / 'OneDrive' / 'classbuddy.db'
        )
        Path(onedrive_path).parent.mkdir(parents=True)
        conn = sqlite3.connect(onedrive_path)
        try:
            conn.execute('PRAGMA journal_mode = WAL')
        finally:
            conn.close()

        with patch.object(database, 'DATABASE_PATH', onedrive_path):
            with patch.dict(
                os.environ,
                {'CLASSBUDDY_SQLITE_WAL': 'false'}
            ):
                with self.assertRaisesRegex(RuntimeError, 'OneDrive'):
                    database.init_db()

    def test_wal_can_be_enabled_for_local_database(self):
        local_path = str(
            Path(self.temp_dir.name) / 'local' / 'classbuddy.db'
        )
        with patch.object(database, 'DATABASE_PATH', local_path):
            with patch.dict(
                os.environ,
                {'CLASSBUDDY_SQLITE_WAL': 'true'}
            ):
                database.init_db()
                with database.managed_connection() as conn:
                    journal_mode = conn.execute(
                        'PRAGMA journal_mode'
                    ).fetchone()[0]

        self.assertEqual(journal_mode.lower(), 'wal')

    def test_scheduler_process_lock_prevents_duplicates_and_releases(self):
        first_lock = acquire_process_lock(
            'classbuddy-reminder-test',
            self.database_path
        )
        try:
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                acquire_process_lock(
                    'classbuddy-reminder-test',
                    self.database_path
                )
        finally:
            first_lock.close()

        second_lock = acquire_process_lock(
            'classbuddy-reminder-test',
            self.database_path
        )
        second_lock.close()

    def test_scheduler_lock_prevents_another_process(self):
        resource = str(Path(self.temp_dir.name) / 'shared-resource.db')
        lock = acquire_process_lock(
            'classbuddy-reminder-subprocess-test',
            resource
        )
        child_code = '''
import sys
from utils.process_lock import acquire_process_lock
try:
    acquire_process_lock(sys.argv[1], sys.argv[2])
except RuntimeError as error:
    print(str(error))
else:
    print("UNEXPECTED_ACQUIRE")
    raise SystemExit(2)
'''
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    '-c',
                    child_code,
                    'classbuddy-reminder-subprocess-test',
                    resource
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False
            )
        finally:
            lock.close()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already running', result.stdout)

    def test_scheduler_lock_rejects_another_app_instance(self):
        first_app = Flask('first-app')
        second_app = Flask('second-app')
        for app in (first_app, second_app):
            app.config.update({
                'DEBUG': False,
                'REMINDER_SCHEDULER_ENABLED': True,
                'REMINDER_POLL_INTERVAL_SECONDS': 5,
                'REMINDER_TIMEZONE': 'Asia/Kolkata',
            })

        first_scheduler = start_reminder_scheduler(first_app)
        self.assertIsNotNone(first_scheduler)
        try:
            with patch.object(second_app.logger, 'exception') as log_exception:
                self.assertIsNone(start_reminder_scheduler(second_app))
            log_exception.assert_called_once()
            self.assertNotIn(
                SCHEDULER_EXTENSION_KEY,
                second_app.extensions
            )
        finally:
            first_scheduler.shutdown(wait=True)

        second_scheduler = start_reminder_scheduler(second_app)
        self.assertIsNotNone(second_scheduler)
        second_scheduler.shutdown(wait=True)

    def test_debug_reloader_parent_does_not_start_scheduler(self):
        app = Flask('debug-reloader-parent')
        app.config.update({
            'DEBUG': True,
            'REMINDER_SCHEDULER_ENABLED': True,
            'REMINDER_TIMEZONE': 'Asia/Kolkata',
            'REMINDER_POLL_INTERVAL_SECONDS': 5,
        })

        with patch.dict(os.environ, {'WERKZEUG_RUN_MAIN': 'false'}):
            self.assertIsNone(start_reminder_scheduler(app))
        self.assertNotIn(SCHEDULER_EXTENSION_KEY, app.extensions)

        with patch.dict(os.environ, {'WERKZEUG_RUN_MAIN': 'true'}):
            scheduler = start_reminder_scheduler(app)
        self.assertIsNotNone(scheduler)
        scheduler.shutdown(wait=True)


if __name__ == '__main__':
    unittest.main()
