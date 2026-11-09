import logging
import smtplib
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from flask import Flask

from config import Config
from services.email_service import (
    EmailDeliveryError,
    build_class_reminder_message,
    send_email,
    send_test_email,
)
from services.reminder_scheduler import (
    SCHEDULER_EXTENSION_KEY,
    SCHEDULER_JOB_ID,
    find_due_classes,
    send_due_reminders,
    start_reminder_scheduler,
)
from utils.database import get_db_connection, init_db

TIMEZONE = ZoneInfo('Asia/Kolkata')


class ReminderSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / 'classbuddy.db')
        self.database_patch = patch(
            'utils.database.DATABASE_PATH',
            self.database_path
        )
        self.database_patch.start()
        init_db()

        conn = get_db_connection()
        conn.execute(
            '''INSERT INTO students (id, name, email, password)
               VALUES (1, 'Student One', 'student@example.com', 'hash')'''
        )
        conn.execute(
            '''INSERT INTO students (id, name, email, password)
               VALUES (2, 'Student Two', 'student-two@example.com', 'hash')'''
        )
        conn.execute(
            '''INSERT INTO schedule
               (id, student_id, subject, faculty, room, day, start_time, end_time)
               VALUES (1, 1, 'Computer Networks', 'Faculty', 'Room 1',
                       'Monday', '10:00', '11:00')'''
        )
        conn.commit()
        conn.close()

        self.app = Flask(__name__)
        self.app.config.update({
            'TESTING': True,
            'DEBUG': False,
            'REMINDER_SCHEDULER_ENABLED': True,
            'REMINDER_POLL_INTERVAL_SECONDS': 5,
            'REMINDER_MINUTES_BEFORE': 15,
            'REMINDER_TIMEZONE': 'Asia/Kolkata',
            'MAIL_SERVER': 'smtp.example.com',
            'MAIL_PORT': 587,
            'MAIL_USE_TLS': True,
            'MAIL_USERNAME': 'smtp-user',
            'MAIL_PASSWORD': 'test-only-smtp-password',
            'MAIL_DEFAULT_SENDER': 'sender@example.com',
            'MAIL_SENDER_NAME': 'ClassBuddy Test',
            'MAIL_TIMEOUT': 15,
        })
        self.app.logger.setLevel(logging.CRITICAL)
        self.monday = date(2026, 10, 26)
        self.now = datetime(2026, 10, 26, 9, 45, tzinfo=TIMEZONE)

    def tearDown(self):
        scheduler = self.app.extensions.get(SCHEDULER_EXTENSION_KEY)
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=True)
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def test_reminder_occurrence_is_fifteen_minutes_before_class(self):
        classes = [{
            'id': 1,
            'subject': 'Computer Networks',
            'day': 'Monday',
            'start_time': '10:00',
        }]

        due = find_due_classes(self.now, classes, TIMEZONE)

        self.assertEqual(len(due), 1)
        self.assertEqual(
            due[0]['class_start'] - due[0]['reminder_at'],
            timedelta(minutes=15)
        )
        self.assertEqual(due[0]['reminder_at'], self.now)

    def test_correct_student_receives_subject_and_class_time(self):
        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, self.now)

        send_message.assert_called_once()
        args = send_message.call_args.args
        self.assertEqual(args[2], 'student@example.com')
        self.assertEqual(args[3]['subject'], 'Computer Networks')
        self.assertEqual(args[4], self.now)

        message = build_class_reminder_message(
            'Student One',
            {
                'subject': 'Computer Networks',
                'start_time': '10:00',
                'end_time': '11:00',
                'room': 'Room 1',
            },
            '10:00',
            '09:45',
            self.monday,
            reminder_minutes=15
        )
        self.assertEqual(
            message['Subject'],
            'ClassBuddy – Upcoming Class Reminder'
        )
        plain_body = message.get_body(
            preferencelist=('plain',)
        ).get_content()
        self.assertIn(
            'This is a reminder about your upcoming class.',
            plain_body
        )
        self.assertIn('Your class will start in 15 minutes.', plain_body)
        self.assertIn('Date: October 26, 2026', plain_body)
        self.assertIn('Time: 10:00 AM', plain_body)
        self.assertIn('Room: Room 1', plain_body)
        self.assertIn(
            'ClassBuddy – Smart Class Attendance & Reminder System',
            plain_body
        )
        self.assertIsNotNone(message.get_body(preferencelist=('html',)))
        self.assertIn(
            'Computer Networks',
            message.get_body(preferencelist=('html',)).get_content()
        )

    def test_scheduler_sends_registered_recipient_via_smtp(self):
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp

        with patch(
            'services.email_service.smtplib.SMTP',
            return_value=smtp
        ) as smtp_connection:
            send_due_reminders(self.app, self.now)

        smtp_connection.assert_called_once_with(
            'smtp.example.com',
            587,
            timeout=15
        )
        smtp.starttls.assert_called_once_with()
        smtp.login.assert_called_once_with('smtp-user', 'test-only-smtp-password')
        sent_message = smtp.send_message.call_args.args[0]
        self.assertEqual(sent_message['From'], 'ClassBuddy Test <sender@example.com>')
        self.assertEqual(sent_message['To'], 'student@example.com')
        self.assertEqual(
            sent_message['Subject'],
            'ClassBuddy – Upcoming Class Reminder'
        )
        self.assertIn(
            'Your class will start in 15 minutes.',
            sent_message.get_body(preferencelist=('plain',)).get_content()
        )
        self.assertIn(
            'Computer Networks',
            sent_message.get_body(preferencelist=('html',)).get_content()
        )

    def test_each_supported_reminder_time_is_used_for_its_student(self):
        for minutes in (5, 10, 15, 20, 30):
            with self.subTest(minutes=minutes):
                conn = get_db_connection()
                conn.execute('DELETE FROM email_reminder_history')
                conn.execute(
                    '''INSERT INTO student_notification_settings
                       (student_id, email_reminders_enabled, reminder_minutes)
                       VALUES (1, 1, ?)
                       ON CONFLICT(student_id) DO UPDATE SET
                           email_reminders_enabled = 1,
                           reminder_minutes = excluded.reminder_minutes''',
                    (minutes,)
                )
                conn.commit()
                conn.close()
                reminder_time = self.now.replace(
                    hour=10,
                    minute=0
                ) - timedelta(minutes=minutes)

                with patch(
                    'services.reminder_scheduler.send_class_reminder'
                ) as send_message:
                    send_due_reminders(self.app, reminder_time)

                send_message.assert_called_once()
                self.assertEqual(send_message.call_args.args[4], reminder_time)
                self.assertEqual(
                    send_message.call_args.args[5]
                    - send_message.call_args.args[4],
                    timedelta(minutes=minutes)
                )

    def test_students_receive_reminders_at_their_individual_times(self):
        conn = get_db_connection()
        conn.execute(
            '''INSERT INTO schedule
               (id, student_id, subject, faculty, room, day, start_time, end_time)
               VALUES (2, 2, 'Student Two Class', 'Faculty 2', 'Room 2',
                       'Monday', '10:00', '11:00')'''
        )
        conn.execute(
            "UPDATE schedule SET start_time = '09:55' WHERE id = 1"
        )
        conn.execute(
            '''INSERT INTO student_notification_settings
               (student_id, email_reminders_enabled, reminder_minutes)
               VALUES (1, 1, 5), (2, 1, 10)'''
        )
        conn.commit()
        conn.close()

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(
                self.app,
                datetime(2026, 10, 26, 9, 50, tzinfo=TIMEZONE)
            )

        self.assertEqual(send_message.call_count, 2)
        delivery_offsets = {
            call.args[2]: int(
                (call.args[5] - call.args[4]).total_seconds() // 60
            )
            for call in send_message.call_args_list
        }
        self.assertEqual(delivery_offsets, {
            'student@example.com': 5,
            'student-two@example.com': 10,
        })

    def test_changed_reminder_time_takes_effect_on_next_scan(self):
        conn = get_db_connection()
        conn.execute(
            '''INSERT INTO student_notification_settings
               (student_id, email_reminders_enabled, reminder_minutes)
               VALUES (1, 1, 5)
               ON CONFLICT(student_id) DO UPDATE SET
                   reminder_minutes = excluded.reminder_minutes'''
        )
        conn.commit()
        conn.close()
        now = datetime(2026, 10, 26, 9, 40, tzinfo=TIMEZONE)

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, now)

        send_message.assert_not_called()
        conn = get_db_connection()
        conn.execute(
            '''UPDATE student_notification_settings
               SET reminder_minutes = 20 WHERE student_id = 1'''
        )
        conn.commit()
        conn.close()

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, now)

        send_message.assert_called_once()
        self.assertEqual(
            send_message.call_args.args[5] - send_message.call_args.args[4],
            timedelta(minutes=20)
        )

    def test_two_students_receive_only_their_own_class_reminders(self):
        conn = get_db_connection()
        conn.execute(
            '''INSERT INTO schedule
               (id, student_id, subject, faculty, room, day, start_time, end_time)
               VALUES (2, 2, 'Student Two Class', 'Faculty 2', 'Room 2',
                       'Monday', '10:00', '11:00')'''
        )
        conn.commit()
        conn.close()

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, self.now)

        self.assertEqual(send_message.call_count, 2)
        deliveries = {
            call.args[2]: call.args[3]['subject']
            for call in send_message.call_args_list
        }
        self.assertEqual(deliveries, {
            'student@example.com': 'Computer Networks',
            'student-two@example.com': 'Student Two Class',
        })

    def test_disabled_email_reminders_are_not_claimed_or_sent(self):
        conn = get_db_connection()
        conn.execute(
            '''INSERT INTO student_notification_settings
               (student_id, email_reminders_enabled) VALUES (1, 0)'''
        )
        conn.commit()
        conn.close()

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, self.now)

        send_message.assert_not_called()
        conn = get_db_connection()
        history_count = conn.execute(
            'SELECT COUNT(*) FROM email_reminder_history'
        ).fetchone()[0]
        conn.close()
        self.assertEqual(history_count, 0)

    def test_updated_subject_is_used_for_an_upcoming_class(self):
        conn = get_db_connection()
        conn.execute(
            "UPDATE schedule SET subject = 'Updated Networks' WHERE id = 1"
        )
        conn.commit()
        conn.close()

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, self.now)

        send_message.assert_called_once()
        self.assertEqual(
            send_message.call_args.args[3]['subject'],
            'Updated Networks'
        )

    def test_scheduler_default_poll_interval_is_thirty_seconds(self):
        self.assertEqual(Config.REMINDER_POLL_INTERVAL_SECONDS, 30)

    def test_deleted_class_does_not_send_a_reminder(self):
        conn = get_db_connection()
        conn.execute('DELETE FROM schedule WHERE id = 1')
        conn.commit()
        conn.close()

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, self.now)

        send_message.assert_not_called()

    def test_past_classes_do_not_receive_reminders(self):
        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(
                self.app,
                datetime(2026, 10, 26, 10, 1, tzinfo=TIMEZONE)
            )

        send_message.assert_not_called()

    def test_duplicate_occurrence_is_sent_only_once(self):
        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, self.now)
            send_due_reminders(self.app, self.now)

        send_message.assert_called_once()
        conn = get_db_connection()
        row = conn.execute(
            'SELECT status FROM email_reminder_history'
        ).fetchone()
        conn.close()
        self.assertEqual(row['status'], 'sent')

    def test_in_progress_claim_is_not_retried_after_restart(self):
        conn = get_db_connection()
        conn.execute(
            '''INSERT INTO email_reminder_history
               (student_id, schedule_id, class_start, status, attempted_at)
               VALUES (1, 1, '2026-10-26T10:00+05:30', 'sending',
                       '2026-10-26T09:55:00+05:30')'''
        )
        conn.commit()
        conn.close()

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, self.now + timedelta(seconds=90))

        send_message.assert_not_called()

    def test_registered_email_receives_reminder_without_student_oauth(self):
        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, self.now)

        send_message.assert_called_once()
        self.assertEqual(send_message.call_args.args[2], 'student@example.com')

    def test_all_weekdays_are_matched_to_their_own_class_date(self):
        monday = date(2026, 10, 26)
        classes = [{
            'id': 1,
            'subject': 'Computer Networks',
            'day': 'Monday',
            'start_time': '10:00',
        }]

        for day_offset in range(7):
            class_date = monday + timedelta(days=day_offset)
            classes[0]['day'] = class_date.strftime('%A')
            now = datetime.combine(
                class_date,
                time(9, 45),
                tzinfo=TIMEZONE
            )
            with self.subTest(day=classes[0]['day']):
                due = find_due_classes(now, classes, TIMEZONE)
                self.assertEqual(len(due), 1)
                self.assertEqual(
                    due[0]['class_start'].date(),
                    class_date
                )

    def test_class_just_after_midnight_uses_the_next_day(self):
        saturday_night = datetime(
            2026, 10, 31, 23, 46, tzinfo=TIMEZONE
        )
        classes = [{
            'id': 2,
            'subject': 'Early Class',
            'day': 'Sunday',
            'start_time': '00:01',
        }]

        due = find_due_classes(saturday_night, classes, TIMEZONE)

        self.assertEqual(len(due), 1)
        self.assertEqual(
            due[0]['class_start'],
            datetime(2026, 11, 1, 0, 1, tzinfo=TIMEZONE)
        )

    def test_invalid_class_time_is_skipped_without_stopping_the_scan(self):
        conn = get_db_connection()
        conn.execute(
            "UPDATE schedule SET start_time = 'not-a-time' WHERE id = 1"
        )
        conn.execute(
            '''INSERT INTO schedule
               (id, student_id, subject, faculty, room, day, start_time, end_time)
               VALUES (2, 1, 'Valid Class', 'Faculty', 'Room 2',
                       'Monday', '10:00', '11:00')'''
        )
        conn.commit()
        conn.close()

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, self.now)

        send_message.assert_called_once()
        self.assertEqual(
            send_message.call_args.args[3]['subject'],
            'Valid Class'
        )

    def test_provider_failure_is_recorded_without_raising(self):
        with patch(
            'services.reminder_scheduler.send_class_reminder',
            side_effect=RuntimeError('temporary test failure')
        ):
            send_due_reminders(self.app, self.now)

        conn = get_db_connection()
        row = conn.execute(
            'SELECT status, error_type FROM email_reminder_history'
        ).fetchone()
        conn.close()
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['error_type'], 'RuntimeError')

    def test_scheduler_reuses_one_job(self):
        first = start_reminder_scheduler(self.app)
        second = start_reminder_scheduler(self.app)

        self.assertIs(first, second)
        jobs = first.get_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].id, SCHEDULER_JOB_ID)
        self.assertEqual(jobs[0].trigger.__class__.__name__, 'CronTrigger')

    def test_flask_app_factory_starts_the_scheduler(self):
        with patch('utils.database.DATABASE_PATH', self.database_path):
            from app import create_app

            flask_app = create_app({
                'DEBUG': False,
                'REMINDER_SCHEDULER_ENABLED': True,
            })

        scheduler = flask_app.extensions[SCHEDULER_EXTENSION_KEY]
        self.assertTrue(scheduler.running)
        self.assertEqual(
            [job.id for job in scheduler.get_jobs()],
            [SCHEDULER_JOB_ID]
        )
        scheduler.shutdown(wait=True)

    def test_timetable_update_is_seen_by_the_next_scan(self):
        conn = get_db_connection()
        conn.execute(
            "UPDATE schedule SET start_time = '11:00' WHERE id = 1"
        )
        conn.commit()
        conn.close()
        updated_reminder_time = datetime(
            2026, 10, 26, 10, 45, tzinfo=TIMEZONE
        )

        with patch(
            'services.reminder_scheduler.send_class_reminder'
        ) as send_message:
            send_due_reminders(self.app, updated_reminder_time)

        send_message.assert_called_once()
        self.assertEqual(
            send_message.call_args.args[3]['start_time'],
            '11:00'
        )

    def test_test_email_uses_smtp(self):
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        with patch(
            'services.email_service.smtplib.SMTP',
            return_value=smtp
        ):
            send_test_email(
                self.app.config,
                'student@example.com',
                'Student One'
            )

        sent_message = smtp.send_message.call_args.args[0]
        self.assertEqual(
            sent_message['Subject'],
            'ClassBuddy email configuration test'
        )
        self.assertEqual(sent_message['To'], 'student@example.com')
        self.assertIn(
            'Student One',
            sent_message.get_body(preferencelist=('plain',)).get_content()
        )

    def test_smtp_authentication_failure_has_safe_diagnostic(self):
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(
            535,
            b'password must not be logged'
        )
        with patch(
            'services.email_service.smtplib.SMTP',
            return_value=smtp
        ):
            with self.assertRaises(EmailDeliveryError) as context:
                send_test_email(
                    self.app.config,
                    'student@example.com',
                    'Student One'
                )

        error = context.exception
        self.assertEqual(error.stage, 'SMTP authentication')
        self.assertEqual(error.exception_type, 'SMTPAuthenticationError')
        self.assertEqual(error.provider_status, 535)
        self.assertIn('SMTP server', error.diagnostic)
        self.assertNotIn(
            self.app.config['MAIL_PASSWORD'],
            error.diagnostic
        )
        self.assertNotIn('password must not be logged', error.diagnostic)

    def test_missing_smtp_configuration_names_missing_keys_only(self):
        config = dict(self.app.config)
        config['MAIL_SERVER'] = ''
        config['MAIL_USERNAME'] = ''
        config['MAIL_PASSWORD'] = ''
        config['MAIL_DEFAULT_SENDER'] = ''

        with self.assertRaises(ValueError) as context:
            send_email(config, 'student@example.com', MagicMock())

        self.assertEqual(
            str(context.exception),
            'Missing SMTP email configuration: MAIL_SERVER, MAIL_USERNAME, '
            'MAIL_PASSWORD, MAIL_DEFAULT_SENDER.'
        )


if __name__ == '__main__':
    unittest.main()
