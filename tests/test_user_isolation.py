import logging
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from models.schedule import Schedule
from models.student import Student
from utils import database


class UserTimetableIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / 'legacy.db')
        self.database_patch = patch.object(
            database,
            'DATABASE_PATH',
            self.database_path
        )
        self.database_patch.start()

        conn = sqlite3.connect(self.database_path)
        conn.executescript('''
            CREATE TABLE students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            );
            CREATE TABLE schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                faculty TEXT NOT NULL,
                room TEXT NOT NULL,
                day TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL
            );
        ''')
        conn.execute(
            'INSERT INTO students (id, name, email, password) VALUES (?, ?, ?, ?)',
            (
                11,
                'Student A',
                'a@example.com',
                generate_password_hash('password-a')
            )
        )
        conn.execute(
            'INSERT INTO students (id, name, email, password) VALUES (?, ?, ?, ?)',
            (
                22,
                'Student B',
                'b@example.com',
                generate_password_hash('password-b')
            )
        )
        today = datetime.now().strftime('%A')
        conn.execute(
            '''INSERT INTO schedule
               (id, subject, faculty, room, day, start_time, end_time)
               VALUES (100, 'Legacy unassigned class', 'Faculty', 'Room L',
                       ?, '09:00', '10:00')''',
            (today,)
        )
        conn.commit()
        conn.close()

        import services.reminder_scheduler as reminder_scheduler

        with patch.object(reminder_scheduler, 'start_reminder_scheduler'):
            from app import create_app

            self.app = create_app({
                'TESTING': True,
                'DEBUG': False,
                'SECRET_KEY': 'test-only-secret',
                'REMINDER_SCHEDULER_ENABLED': False,
            })

        self.app.logger.setLevel(logging.CRITICAL)
        self.student_a_class = Schedule.add(
            11,
            'Student A Only',
            'Faculty A',
            'Room A',
            today,
            '12:00',
            '13:00'
        )
        self.student_b_class = Schedule.add(
            22,
            'Student B Only',
            'Faculty B',
            'Room B',
            today,
            '14:00',
            '15:00'
        )
        conn = database.get_db_connection()
        self.student_a_class = conn.execute(
            "SELECT id FROM schedule WHERE student_id = 11 AND subject = 'Student A Only'"
        ).fetchone()['id']
        self.student_b_class = conn.execute(
            "SELECT id FROM schedule WHERE student_id = 22 AND subject = 'Student B Only'"
        ).fetchone()['id']
        self.legacy_class = conn.execute(
            'SELECT id, subject, student_id FROM schedule WHERE id = 100'
        ).fetchone()
        conn.close()
        self.client = self.app.test_client()
        self.today = today

    def tearDown(self):
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def login_as(self, email, password, expected_id):
        response = self.client.post(
            '/login',
            data={'email': email, 'password': password},
            follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session.get('_user_id'), str(expected_id))
        return response

    def test_google_auth_routes_and_buttons_are_removed(self):
        for path in (
            '/auth/google',
            '/google/login',
            '/auth/google/callback',
            '/google/callback',
            '/auth/gmail/connect',
            '/auth/gmail/callback',
            '/auth/gmail/disconnect',
            '/settings/gmail/connect',
            '/settings/gmail/callback',
            '/settings/gmail/disconnect',
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

        login_page = self.client.get('/login')
        register_page = self.client.get('/register')
        for page in (login_page, register_page):
            self.assertNotIn(b'bi-google', page.data)
            self.assertNotIn(b'Continue with Google', page.data)
            self.assertNotIn(b'Sign up with Google', page.data)

    def test_legacy_rows_are_preserved_unassigned(self):
        self.assertEqual(self.legacy_class['subject'], 'Legacy unassigned class')
        self.assertIsNone(self.legacy_class['student_id'])

        conn = database.get_db_connection()
        rows = conn.execute(
            'SELECT COUNT(*) AS count FROM schedule WHERE student_id IS NULL'
        ).fetchone()
        conn.close()
        self.assertEqual(rows['count'], 1)

    def test_student_a_and_b_see_only_their_own_timetable(self):
        response_a = self.login_as(
            'a@example.com',
            'password-a',
            11
        )
        page_a = self.client.get(f'/schedule?day={self.today}')
        self.assertIn(b'Student A Only', page_a.data)
        self.assertNotIn(b'Student B Only', page_a.data)
        self.assertNotIn(b'Legacy unassigned class', page_a.data)
        self.assertIn(b'Student A Only', response_a.data)
        self.assertNotIn(b'Student B Only', response_a.data)

        self.client.get('/logout')
        with self.client.session_transaction() as browser_session:
            browser_session['cached_schedule'] = 'Student A Only'
            browser_session['_user_id'] = '11'
        response_b = self.login_as(
            'b@example.com',
            'password-b',
            22
        )
        with self.client.session_transaction() as browser_session:
            self.assertNotIn('cached_schedule', browser_session)
        page_b = self.client.get(f'/schedule?day={self.today}')
        self.assertIn(b'Student B Only', page_b.data)
        self.assertNotIn(b'Student A Only', page_b.data)
        self.assertNotIn(b'Legacy unassigned class', page_b.data)
        self.assertIn(b'Student B Only', response_b.data)
        self.assertNotIn(b'Student A Only', response_b.data)

    def test_registration_login_and_logout(self):
        registration = self.client.post(
            '/register',
            data={
                'name': 'New Student',
                'email': 'new-student@example.com',
                'password': 'new-student-password',
                'confirm_password': 'new-student-password',
            },
            follow_redirects=True
        )
        self.assertIn(b'Account created successfully.', registration.data)

        with database.managed_connection() as conn:
            row = conn.execute(
                'SELECT id, name, email FROM students WHERE email = ?',
                ('new-student@example.com',)
            ).fetchone()
        self.assertEqual(row['id'], 23)
        self.assertEqual(row['name'], 'New Student')
        self.assertEqual(row['email'], 'new-student@example.com')
        with database.managed_connection() as conn:
            password_hash = conn.execute(
                'SELECT password FROM students WHERE id = ?',
                (23,)
            ).fetchone()['password']
        self.assertNotEqual(password_hash, 'new-student-password')
        self.assertTrue(
            Student.find_by_id(23).verify_password('new-student-password')
        )

        login = self.login_as(
            'new-student@example.com',
            'new-student-password',
            23
        )
        self.assertIn(b'Good', login.data)

        response = self.client.get('/logout')
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as browser_session:
            self.assertNotIn('_user_id', browser_session)

    def test_registration_requires_valid_email_and_matching_passwords(self):
        invalid_email = self.client.post(
            '/register',
            data={
                'name': 'Invalid Email',
                'email': 'not-an-email',
                'password': 'secure-password',
                'confirm_password': 'secure-password',
            },
            follow_redirects=True
        )
        self.assertIn(b'Enter a valid email address.', invalid_email.data)

        mismatch = self.client.post(
            '/register',
            data={
                'name': 'Mismatch',
                'email': 'mismatch@example.com',
                'password': 'secure-password',
                'confirm_password': 'different-password',
            },
            follow_redirects=True
        )
        self.assertIn(b'Passwords do not match.', mismatch.data)
        self.assertIsNone(Student.find_by_email('mismatch@example.com'))

    def test_attendance_recording_and_risk_prediction(self):
        self.login_as('a@example.com', 'password-a', 11)
        attendance_response = self.client.post(
            '/attendance/mark',
            data={
                'subject': 'Student A Only',
                'status': 'Present',
                'date': date.today().isoformat(),
            },
            follow_redirects=True
        )
        self.assertIn(b'Marked Present for Student A Only', attendance_response.data)

        with database.managed_connection() as conn:
            row = conn.execute(
                'SELECT student_id, subject, status FROM attendance '
                'WHERE student_id = ? AND subject = ?',
                (11, 'Student A Only')
            ).fetchone()
        self.assertEqual(row['student_id'], 11)
        self.assertEqual(row['status'], 'Present')

        prediction_response = self.client.post('/api/predictions/refresh')
        self.assertEqual(prediction_response.status_code, 200)
        self.assertTrue(prediction_response.json['success'])
        self.assertEqual(
            prediction_response.json['predictions'][0]['subject'],
            'Student A Only'
        )

    def test_web_app_factory_does_not_start_scheduler_by_default(self):
        from app import create_app

        with patch(
            'services.reminder_scheduler.start_reminder_scheduler'
        ) as start_scheduler:
            create_app({'DEBUG': False})

        start_scheduler.assert_not_called()

    def test_database_initialization_precedes_scheduler_startup(self):
        from app import create_app

        startup_order = []

        def initialize_database():
            startup_order.append('database')

        def start_scheduler(app):
            startup_order.append(f"scheduler:{app.config['DEBUG']}")

        with patch('app.init_db', side_effect=initialize_database):
            with patch(
                'app.start_reminder_scheduler',
                side_effect=start_scheduler
            ):
                create_app({
                    'DEBUG': False,
                    'REMINDER_SCHEDULER_ENABLED': True,
                })

        self.assertEqual(startup_order, ['database', 'scheduler:False'])

    def test_logout_clears_session_and_protected_pages_require_login(self):
        self.login_as('a@example.com', 'password-a', 11)
        logout_response = self.client.get('/logout')

        self.assertEqual(logout_response.status_code, 302)
        self.assertIn('no-store', logout_response.headers['Cache-Control'])
        with self.client.session_transaction() as browser_session:
            self.assertNotIn('_user_id', browser_session)

        for path in ('/dashboard', '/schedule', '/attendance', '/settings'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/login', response.headers['Location'])

    def test_authenticated_pages_have_no_store_cache_headers(self):
        self.login_as('a@example.com', 'password-a', 11)

        for path in ('/dashboard', f'/schedule?day={self.today}'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn('no-store', response.headers['Cache-Control'])
                self.assertIn('private', response.headers['Cache-Control'])
                self.assertIn('Cookie', response.headers['Vary'])

    def test_student_b_cannot_edit_or_delete_student_a_record(self):
        self.login_as('b@example.com', 'password-b', 22)
        payload = {
            'subject': 'Hacked Subject',
            'faculty': 'Faculty',
            'room': 'Room',
            'day': self.today,
            'start_time': '16:00',
            'end_time': '17:00',
            'selected_day': self.today,
        }

        edit_response = self.client.post(
            f'/schedule/edit/{self.student_a_class}',
            data=payload,
            follow_redirects=True
        )
        self.assertIn(b'Class not found.', edit_response.data)

        delete_response = self.client.post(
            f'/schedule/delete/{self.student_a_class}',
            data={'day': self.today},
            follow_redirects=True
        )
        self.assertIn(b'Class not found.', delete_response.data)

        conn = database.get_db_connection()
        row = conn.execute(
            'SELECT subject, student_id FROM schedule WHERE id = ?',
            (self.student_a_class,)
        ).fetchone()
        conn.close()
        self.assertEqual(row['subject'], 'Student A Only')
        self.assertEqual(row['student_id'], 11)

    def test_student_can_create_edit_and_delete_own_timetable_record(self):
        self.login_as('a@example.com', 'password-a', 11)
        payload = {
            'subject': 'New A Class',
            'faculty': 'Faculty A',
            'room': 'Room A',
            'day': self.today,
            'start_time': '16:00',
            'end_time': '17:00',
            'selected_day': self.today,
        }

        self.client.post('/schedule/add', data=payload)
        conn = database.get_db_connection()
        created = conn.execute(
            "SELECT id, student_id FROM schedule WHERE subject = 'New A Class'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(created)
        self.assertEqual(created['student_id'], 11)

        payload['subject'] = 'Updated A Class'
        self.client.post(
            f"/schedule/edit/{created['id']}",
            data=payload
        )
        conn = database.get_db_connection()
        updated = conn.execute(
            'SELECT subject, student_id FROM schedule WHERE id = ?',
            (created['id'],)
        ).fetchone()
        conn.close()
        self.assertEqual(updated['subject'], 'Updated A Class')
        self.assertEqual(updated['student_id'], 11)

        self.client.post(
            f"/schedule/delete/{created['id']}",
            data={'day': self.today}
        )
        self.assertIsNone(Schedule.get_by_id(11, created['id']))

    def test_browser_student_id_parameters_do_not_change_timetable_scope(self):
        self.login_as('b@example.com', 'password-b', 22)

        response = self.client.get(
            f'/schedule?day={self.today}&student_id=11'
        )

        self.assertIn(b'Student B Only', response.data)
        self.assertNotIn(b'Student A Only', response.data)

    def test_email_reminder_preference_is_persisted_per_student(self):
        self.login_as('a@example.com', 'password-a', 11)
        settings = self.client.get('/settings')
        self.assertIn(b'value="enabled"', settings.data)
        self.assertIn(b'value="15" selected', settings.data)
        self.assertIn(b'mailto:a@example.com', settings.data)

        updated = self.client.post(
            '/settings/email-reminders',
            data={
                'email_reminders_enabled': 'enabled',
                'reminder_minutes': '5',
            },
            follow_redirects=True
        )
        self.assertIn(
            b'Settings saved successfully! Your class reminders will be sent '
            b'to a@example.com, 5 minutes before each class.',
            updated.data
        )
        self.assertIn(b'value="5" selected', updated.data)

        with database.managed_connection() as conn:
            preference_a = conn.execute(
                '''SELECT email_reminders_enabled, reminder_minutes, updated_at
                   FROM student_notification_settings WHERE student_id = 11'''
            ).fetchone()
            preference_b = conn.execute(
                '''SELECT email_reminders_enabled, reminder_minutes
                   FROM student_notification_settings WHERE student_id = 22'''
            ).fetchone()

        self.assertEqual(preference_a['email_reminders_enabled'], 1)
        self.assertEqual(preference_a['reminder_minutes'], 5)
        self.assertTrue(preference_a['updated_at'])
        self.assertEqual(preference_b['email_reminders_enabled'], 1)
        self.assertEqual(preference_b['reminder_minutes'], 15)

        self.client.get('/logout')
        self.login_as('a@example.com', 'password-a', 11)
        self.assertIn(b'value="5" selected', self.client.get('/settings').data)

    def test_all_reminder_times_and_enable_disable_settings_are_saved(self):
        self.login_as('a@example.com', 'password-a', 11)
        for minutes in (5, 10, 15, 20, 30):
            response = self.client.post(
                '/settings/email-reminders',
                data={
                    'email_reminders_enabled': 'enabled',
                    'reminder_minutes': str(minutes),
                },
                follow_redirects=True
            )
            with self.subTest(minutes=minutes):
                self.assertIn(
                    f'{minutes} minutes before each class.'.encode(),
                    response.data
                )
                self.assertIn(
                    f'value="{minutes}" selected'.encode(),
                    response.data
                )

        disabled = self.client.post(
            '/settings/email-reminders',
            data={
                'email_reminders_enabled': 'disabled',
                'reminder_minutes': '20',
            },
            follow_redirects=True
        )
        self.assertIn(
            b'Settings saved successfully! Email reminders disabled.',
            disabled.data
        )
        self.assertIn(b'value="disabled"', disabled.data)
        self.assertIn(b'value="20" selected', disabled.data)

        enabled = self.client.post(
            '/settings/email-reminders',
            data={
                'email_reminders_enabled': 'enabled',
                'reminder_minutes': '20',
            },
            follow_redirects=True
        )
        self.assertIn(b'value="enabled"', enabled.data)
        with database.managed_connection() as conn:
            settings = conn.execute(
                '''SELECT email_reminders_enabled, reminder_minutes
                   FROM student_notification_settings WHERE student_id = 11'''
            ).fetchone()
        self.assertEqual(settings['email_reminders_enabled'], 1)
        self.assertEqual(settings['reminder_minutes'], 20)

    def test_invalid_reminder_time_is_rejected_without_changing_preferences(self):
        self.login_as('a@example.com', 'password-a', 11)
        response = self.client.post(
            '/settings/email-reminders',
            data={
                'email_reminders_enabled': 'enabled',
                'reminder_minutes': '7',
            },
            follow_redirects=True
        )
        self.assertIn(b'Choose a valid reminder time.', response.data)
        with database.managed_connection() as conn:
            setting = conn.execute(
                '''SELECT reminder_minutes FROM student_notification_settings
                   WHERE student_id = 11'''
            ).fetchone()
        self.assertEqual(setting['reminder_minutes'], 15)

    def test_dashboard_shows_only_logged_in_students_email_and_preference(self):
        self.login_as('a@example.com', 'password-a', 11)
        self.client.post(
            '/settings/email-reminders',
            data={
                'email_reminders_enabled': 'enabled',
                'reminder_minutes': '10',
            }
        )
        dashboard = self.client.get('/dashboard')
        self.assertIn(b'mailto:a@example.com', dashboard.data)
        self.assertIn(b'Sent 10 minutes before class.', dashboard.data)
        self.assertNotIn(b'b@example.com', dashboard.data)

    def test_new_login_loads_fresh_dashboard_data_after_logout(self):
        self.login_as('a@example.com', 'password-a', 11)
        first_dashboard = self.client.get('/dashboard')
        self.assertIn(b'Student A Only', first_dashboard.data)

        self.client.get('/logout')
        second_dashboard = self.login_as(
            'b@example.com',
            'password-b',
            22
        )

        self.assertIn(b'Student B Only', second_dashboard.data)
        self.assertNotIn(b'Student A Only', second_dashboard.data)

    def test_test_email_route_logs_safe_provider_failure_diagnostics(self):
        from services.email_service import EmailDeliveryError

        self.login_as('a@example.com', 'password-a', 11)
        with patch(
            'routes.reminder_routes.send_test_email',
            side_effect=EmailDeliveryError(
                'SMTP authentication',
                'The SMTP server rejected the configured credentials.',
                'SMTPAuthenticationError',
                535
            )
        ):
            with self.assertLogs(self.app.logger, level='ERROR') as logs:
                response = self.client.post(
                    '/settings/test-email',
                    follow_redirects=True
                )

        self.assertIn(
            b'Test email failed during smtp authentication',
            response.data
        )
        log_message = '\n'.join(logs.output)
        self.assertIn('stage=SMTP authentication', log_message)
        self.assertIn('SMTPAuthenticationError', log_message)
        self.assertIn('provider_status=535', log_message)
        self.assertNotIn('test-only-smtp-password', log_message)

    def test_test_email_success_message_uses_only_registered_recipient(self):
        self.login_as('a@example.com', 'password-a', 11)
        with patch('routes.reminder_routes.send_test_email') as send_message:
            response = self.client.post(
                '/settings/test-email',
                follow_redirects=True
            )

        self.assertIn(
            b'Test email sent to your registered email address.',
            response.data
        )
        send_message.assert_called_once_with(
            self.app.config,
            'a@example.com',
            'Student A'
        )

    def test_test_email_configuration_error_identifies_missing_settings(self):
        self.login_as('a@example.com', 'password-a', 11)
        from services.email_service import EmailConfigurationError

        with patch(
            'routes.reminder_routes.send_test_email',
            side_effect=EmailConfigurationError(
                'Missing SMTP email configuration: MAIL_SERVER, '
                'MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER.'
            )
        ):
            response = self.client.post(
                '/settings/test-email',
                follow_redirects=True
            )

        self.assertIn(b'MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD', response.data)
        self.assertIn(b'project .env file', response.data)


if __name__ == '__main__':
    unittest.main()
