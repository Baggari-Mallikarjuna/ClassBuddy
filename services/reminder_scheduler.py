import atexit
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_SCHEDULER_SHUTDOWN

from models.schedule import Schedule
from services.email_service import (
    EmailDeliveryError,
    is_valid_email,
    send_class_reminder,
)
from utils import database
from utils.database import managed_connection
from utils.process_lock import acquire_process_lock

SCHEDULER_EXTENSION_KEY = 'classbuddy_reminder_scheduler'
SCHEDULER_JOB_ID = 'classbuddy_email_reminders'


def _timezone(app):
    return ZoneInfo(app.config['REMINDER_TIMEZONE'])


def find_due_classes(
    now,
    classes,
    timezone,
    minutes_before=15,
    logger=None
):
    """Return current class occurrences whose reminder time has passed."""
    local_now = now.astimezone(timezone)
    due = []
    candidate_dates = (local_now.date(), local_now.date() + timedelta(days=1))

    for class_row in classes:
        try:
            class_time = datetime.strptime(
                class_row['start_time'],
                '%H:%M'
            ).time()
            if class_row['day'] not in {
                'Monday', 'Tuesday', 'Wednesday', 'Thursday',
                'Friday', 'Saturday', 'Sunday'
            }:
                raise ValueError('invalid weekday')
        except (KeyError, TypeError, ValueError):
            if logger:
                try:
                    schedule_id = class_row['id']
                except (KeyError, IndexError, TypeError):
                    schedule_id = 'unknown'
                logger.warning(
                    'Skipping invalid timetable reminder: schedule_id=%s',
                    schedule_id
                )
            continue

        for class_date in candidate_dates:
            if class_date.strftime('%A') != class_row['day']:
                continue

            class_start = datetime.combine(
                class_date,
                class_time,
                tzinfo=timezone
            )
            reminder_at = class_start - timedelta(minutes=minutes_before)
            if reminder_at <= local_now < class_start:
                due.append({
                    'class_row': dict(class_row),
                    'class_start': class_start,
                    'reminder_at': reminder_at
                })

    return due


def _claim_reminder(student_id, occurrence, now):
    class_row = occurrence['class_row']
    class_start = occurrence['class_start'].isoformat(timespec='minutes')
    attempted_at = now.isoformat(timespec='seconds')
    with managed_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current_class = conn.execute(
            '''SELECT * FROM schedule
               WHERE id = ? AND student_id = ?''',
            (class_row['id'], student_id)
        ).fetchone()

        if (
            current_class is None
            or current_class['day'] != class_row['day']
            or current_class['start_time'] != class_row['start_time']
        ):
            return None

        preference = conn.execute(
            '''SELECT email_reminders_enabled, reminder_minutes
               FROM student_notification_settings WHERE student_id = ?''',
            (student_id,)
        ).fetchone()
        if preference is not None and not preference['email_reminders_enabled']:
            return None
        reminder_minutes = (
            preference['reminder_minutes'] if preference is not None else 15
        )
        reminder_at = occurrence['class_start'] - timedelta(
            minutes=reminder_minutes
        )
        if not reminder_at <= now < occurrence['class_start']:
            return None

        student = conn.execute(
            'SELECT id, name, email FROM students WHERE id = ?',
            (student_id,)
        ).fetchone()
        if student is None or not is_valid_email(student['email']):
            return None

        cursor = conn.execute(
            '''INSERT OR IGNORE INTO email_reminder_history
               (student_id, schedule_id, class_start, status, attempted_at)
               VALUES (?, ?, ?, ?, ?)''',
            (
                student_id,
                class_row['id'],
                class_start,
                'sending',
                attempted_at
            )
        )

        if cursor.rowcount == 0:
            return None

        return {
            'student': student,
            'class_row': dict(current_class),
            'recipient': student['email'],
            'reminder_at': reminder_at,
            'reminder_minutes': reminder_minutes,
        }


def _update_attempt(student_id, occurrence, now, status, error_type=None):
    with managed_connection() as conn:
        conn.execute(
            '''UPDATE email_reminder_history
               SET status = ?, attempted_at = ?, error_type = ?
               WHERE student_id = ? AND schedule_id = ? AND class_start = ?''',
            (
                status,
                now.isoformat(timespec='seconds'),
                error_type,
                student_id,
                occurrence['class_row']['id'],
                occurrence['class_start'].isoformat(timespec='minutes')
            )
        )


def send_due_reminders(app, now=None):
    """Send due reminders independently so one delivery error cannot stop others."""
    timezone = _timezone(app)
    current_time = now or datetime.now(timezone)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone)
    else:
        current_time = current_time.astimezone(timezone)

    try:
        with managed_connection() as conn:
            students = conn.execute(
                '''SELECT s.id, s.name, s.email,
                          COALESCE(n.email_reminders_enabled, 1)
                              AS email_reminders_enabled,
                          COALESCE(n.reminder_minutes, 15)
                              AS reminder_minutes
                   FROM students s
                   LEFT JOIN student_notification_settings n
                       ON n.student_id = s.id
                   ORDER BY s.id'''
            ).fetchall()

        for student in students:
            if not student['email_reminders_enabled']:
                continue
            student_classes = Schedule.get_all(student['id'])
            occurrences = find_due_classes(
                current_time,
                student_classes,
                timezone,
                student['reminder_minutes'],
                app.logger
            )
            for occurrence in occurrences:
                class_row = occurrence['class_row']
                try:
                    claimed_student = _claim_reminder(
                        student['id'],
                        occurrence,
                        current_time
                    )
                    if claimed_student is None:
                        continue

                    student_record = claimed_student['student']
                    latest_class = claimed_student['class_row']
                    send_class_reminder(
                        app.config,
                        student_record['name'],
                        claimed_student['recipient'],
                        latest_class,
                        claimed_student['reminder_at'],
                        occurrence['class_start']
                    )
                    _update_attempt(
                        student['id'],
                        occurrence,
                        current_time,
                        'sent'
                    )
                    app.logger.info(
                        'Class reminder sent: student_id=%s schedule_id=%s '
                        'class_start=%s',
                        student['id'],
                        class_row['id'],
                        occurrence['class_start'].isoformat()
                    )
                except Exception as exc:
                    try:
                        _update_attempt(
                            student['id'],
                            occurrence,
                            current_time,
                            'failed',
                            type(exc).__name__
                        )
                    except sqlite3.Error:
                        app.logger.exception(
                            'Could not record email reminder failure: '
                            'student_id=%s schedule_id=%s',
                            student['id'],
                            class_row['id']
                        )
                    if isinstance(exc, EmailDeliveryError):
                        app.logger.error(
                            'Class reminder failed: student_id=%s '
                            'schedule_id=%s stage=%s exception_type=%s '
                            'provider_status=%s diagnostic=%s',
                            student['id'],
                            class_row['id'],
                            exc.stage,
                            exc.exception_type,
                            exc.provider_status,
                            exc.diagnostic
                        )
                    else:
                        app.logger.error(
                            'Class reminder failed: student_id=%s '
                            'schedule_id=%s exception_type=%s',
                            student['id'],
                            class_row['id'],
                            type(exc).__name__
                        )
    except Exception:
        app.logger.exception('Class reminder scan failed.')


def _start_scheduler(app):
    existing = app.extensions.get(SCHEDULER_EXTENSION_KEY)
    if existing is not None and existing.running:
        existing.add_job(
            send_due_reminders,
            'cron',
            second=f"*/{app.config['REMINDER_POLL_INTERVAL_SECONDS']}",
            args=[app],
            id=SCHEDULER_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True
        )
        return existing

    lock_file = acquire_process_lock(
        'classbuddy-reminder',
        database.DATABASE_PATH
    )
    try:
        scheduler = BackgroundScheduler(
            timezone=ZoneInfo(app.config['REMINDER_TIMEZONE'])
        )
        scheduler.add_job(
            send_due_reminders,
            'cron',
            second=f"*/{app.config['REMINDER_POLL_INTERVAL_SECONDS']}",
            args=[app],
            id=SCHEDULER_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True
        )

        def release_process_lock(event):
            if (
                event.code == EVENT_SCHEDULER_SHUTDOWN
                and not lock_file.closed
            ):
                lock_file.close()

        scheduler.add_listener(
            release_process_lock,
            EVENT_SCHEDULER_SHUTDOWN
        )
        scheduler.start()
    except BaseException:
        if not lock_file.closed:
            lock_file.close()
        raise

    app.extensions[SCHEDULER_EXTENSION_KEY] = scheduler
    atexit.register(
        lambda: scheduler.shutdown(wait=False) if scheduler.running else None
    )
    return scheduler


def start_reminder_scheduler(app):
    if not app.config.get('REMINDER_SCHEDULER_ENABLED', True):
        return None

    if app.debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return None

    try:
        return _start_scheduler(app)
    except Exception:
        app.logger.exception('Could not initialize the email reminder scheduler.')
        return None
