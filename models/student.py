import sqlite3

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from utils.database import managed_connection


REMINDER_MINUTE_OPTIONS = (5, 10, 15, 20, 30)


class Student(UserMixin):
    def __init__(self, id, name, email, password):
        self.id = id
        self.name = name
        self.email = email
        self.password = password

    @staticmethod
    def create(name, email, password):
        hashed_pw = generate_password_hash(password)
        try:
            with managed_connection() as conn:
                cursor = conn.execute(
                    'INSERT INTO students (name, email, password) '
                    'VALUES (?, ?, ?)',
                    (name, email, hashed_pw)
                )
                conn.execute(
                    '''INSERT INTO student_notification_settings
                       (student_id, email_reminders_enabled,
                        reminder_minutes)
                       VALUES (?, 1, 15)''',
                    (cursor.lastrowid,)
                )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None

    @staticmethod
    def find_by_email(email):
        with managed_connection() as conn:
            row = conn.execute(
                'SELECT * FROM students WHERE email = ?',
                (email,)
            ).fetchone()
            if row is None:
                return None
            return Student(
                row['id'],
                row['name'],
                row['email'],
                row['password']
            )

    @staticmethod
    def find_by_id(student_id):
        with managed_connection() as conn:
            row = conn.execute(
                'SELECT * FROM students WHERE id = ?',
                (student_id,)
            ).fetchone()
            if row is None:
                return None
            return Student(
                row['id'],
                row['name'],
                row['email'],
                row['password']
            )

    @staticmethod
    def email_reminders_enabled(student_id):
        return Student.email_reminder_settings(student_id)['enabled']

    @staticmethod
    def email_reminder_settings(student_id):
        with managed_connection() as conn:
            row = conn.execute(
                '''SELECT email_reminders_enabled, reminder_minutes, updated_at
                   FROM student_notification_settings
                   WHERE student_id = ?''',
                (student_id,)
            ).fetchone()
        if row is None:
            return {
                'enabled': True,
                'reminder_minutes': 15,
                'updated_at': None,
            }
        return {
            'enabled': bool(row['email_reminders_enabled']),
            'reminder_minutes': row['reminder_minutes'],
            'updated_at': row['updated_at'],
        }

    @staticmethod
    def set_email_reminders_enabled(student_id, enabled):
        settings = Student.email_reminder_settings(student_id)
        Student.set_email_reminder_settings(
            student_id,
            enabled,
            settings['reminder_minutes']
        )

    @staticmethod
    def set_email_reminder_settings(student_id, enabled, reminder_minutes):
        if not isinstance(enabled, bool):
            raise ValueError('Reminder enabled status must be a boolean.')
        if reminder_minutes not in REMINDER_MINUTE_OPTIONS:
            raise ValueError('Reminder time must be one of the available options.')
        with managed_connection() as conn:
            conn.execute(
                '''INSERT INTO student_notification_settings
                   (student_id, email_reminders_enabled, reminder_minutes,
                    updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(student_id) DO UPDATE SET
                       email_reminders_enabled =
                           excluded.email_reminders_enabled,
                       reminder_minutes = excluded.reminder_minutes,
                       updated_at = CURRENT_TIMESTAMP''',
                (student_id, int(enabled), reminder_minutes)
            )

    def verify_password(self, password):
        return check_password_hash(self.password, password)
