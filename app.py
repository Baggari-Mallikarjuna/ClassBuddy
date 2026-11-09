
import math
import os
from datetime import date, datetime

if __name__ == '__main__':
    from dotenv import load_dotenv

    load_dotenv(
        dotenv_path=os.path.join(os.path.dirname(__file__), '.env'),
        override=False
    )
    os.environ.setdefault('REMINDER_SCHEDULER_ENABLED', 'true')

from flask import Flask, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required

from config import Config, load_project_environment
from models.attendance import Attendance
from models.predictor import Prediction
from models.schedule import Schedule
from models.student import Student
from routes.attendance_routes import attendance_bp
from routes.auth_routes import auth_bp
from routes.prediction_routes import prediction_bp
from routes.reminder_routes import reminder_bp
from routes.schedule_routes import schedule_bp
from services.reminder_scheduler import start_reminder_scheduler
from utils.database import DATABASE_PATH, init_db


def _attendance_status(percentage):
    if percentage >= 90:
        return 'Excellent'
    if percentage >= 80:
        return 'Good'
    if percentage >= 75:
        return 'Safe'
    if percentage >= 60:
        return 'At Risk'
    return 'Critical'


def _calculate_required_classes(present, total, target=75):
    if total == 0 or target >= 100:
        return 0

    current_percentage = (present / total) * 100

    if current_percentage >= target:
        return 0

    return max(
        0,
        math.ceil(
            (target * total - 100 * present) / (100 - target)
        )
    )


def _build_subject_cards(student_id):
    cards = []
    subject_data = Attendance.get_subject_wise_percentage(student_id)

    for item in subject_data:
        present_estimate = (
            round((item['percentage'] / 100) * item['total'])
            if item['total']
            else 0
        )

        status = _attendance_status(item['percentage'])

        cards.append({
            'subject': item['subject'],
            'percentage': item['percentage'],
            'present': present_estimate,
            'total': item['total'],
            'status': status,
            'status_class': status.lower().replace(' ', '-')
        })

    return cards


def _build_warning(subject_cards):
    if not subject_cards:
        return None

    worst_subject = min(
        subject_cards,
        key=lambda item: item['percentage']
    )

    if worst_subject['percentage'] >= 75:
        return None

    required = _calculate_required_classes(
        worst_subject['present'],
        worst_subject['total']
    )

    return {
        'subject': worst_subject['subject'],
        'percentage': worst_subject['percentage'],
        'required_classes': required,
        'level': (
            'critical'
            if worst_subject['percentage'] < 60
            else 'warning'
        )
    }


def create_app(config_overrides=None):
    load_project_environment()
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)
    app.config.setdefault('SESSION_PROTECTION', 'strong')
    app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)
    app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')

    init_db()
    app.logger.info('Using SQLite database: %s', DATABASE_PATH)

    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.login_message = (
        'Please log in to access ClassBuddy.'
    )
    login_manager.login_message_category = 'info'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(student_id):
        try:
            return Student.find_by_id(int(student_id))
        except (TypeError, ValueError):
            return None

    app.register_blueprint(auth_bp)
    app.register_blueprint(schedule_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(prediction_bp)
    app.register_blueprint(reminder_bp)
    start_reminder_scheduler(app)

    @app.after_request
    def prevent_private_page_caching(response):
        if request.endpoint != 'static':
            response.headers['Cache-Control'] = 'no-store, private, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            response.vary.add('Cookie')
        return response

    @app.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        return redirect(url_for('auth.login'))

    @app.route('/settings')
    @login_required
    def settings_page():
        reminder_settings = Student.email_reminder_settings(current_user.id)
        return render_template(
            'settings.html',
            default_target=75,
            reminder_minutes_before=reminder_settings['reminder_minutes'],
            email_reminders_enabled=reminder_settings['enabled']
        )

    @app.route('/dashboard')
    @login_required
    def dashboard():
        today_date = date.today()
        today_name = datetime.now().strftime('%A')
        now = datetime.now()

        # Convert SQLite rows into dictionaries before modifying them.
        todays_classes = [
            dict(row)
            for row in Schedule.get_today(current_user.id, today_name)
        ]

        overall_pct = Attendance.get_overall_percentage(
            current_user.id
        )

        recent_attendance = Attendance.get_history(
            current_user.id,
            limit=6
        )

        # Convert predictions to dictionaries before adding fields.
        predictions = [
            dict(prediction)
            for prediction in Prediction.get_for_student(
                current_user.id
            )
        ]

        subject_cards = _build_subject_cards(current_user.id)
        attendance_warning = _build_warning(subject_cards)

        today_records = [
            record
            for record in recent_attendance
            if record['date'] == today_date.isoformat()
        ]

        today_statuses = {
            record['subject']: record['status']
            for record in today_records
        }

        # Find the next upcoming class.
        next_class = None

        for class_row in todays_classes:
            start_time = datetime.strptime(
                class_row['start_time'],
                '%H:%M'
            ).time()

            class_datetime = datetime.combine(
                today_date,
                start_time
            )

            minutes_until = int(
                (class_datetime - now).total_seconds() // 60
            )

            if minutes_until >= 0:
                next_class = {
                    'class_row': class_row,
                    'minutes_until': minutes_until
                }
                break

        # Update timetable dictionaries safely.
        for class_row in todays_classes:
            class_row['attendance_status'] = today_statuses.get(
                class_row['subject'],
                'Pending'
            )

            class_row['is_current'] = (
                next_class is not None
                and class_row['id'] == next_class['class_row']['id']
            )

        # Calculate overall attendance statistics.
        attendance_history = Attendance.get_history(
            current_user.id,
            limit=500
        )

        total_records = len(attendance_history)

        total_present = sum(
            1
            for record in attendance_history
            if record['status'] == 'Present'
        )

        attendance_target = 75

        required_classes = _calculate_required_classes(
            total_present,
            total_records,
            attendance_target
        )

        # Calculate the consecutive present-record streak.
        streak = 0

        for record in attendance_history:
            if record['status'] == 'Present':
                streak += 1
            else:
                break

        # Generate a greeting based on the current time.
        hour = now.hour

        if hour < 12:
            greeting = 'Good morning'
        elif hour < 17:
            greeting = 'Good afternoon'
        else:
            greeting = 'Good evening'

        # Add explanations and recommendations to predictions.
        for prediction in predictions:
            if prediction['risk_level'] == 'High':
                prediction['reason'] = (
                    'Attendance is below the required threshold.'
                )
                prediction['recommendation'] = (
                    'Attend the next 4 classes to improve your attendance.'
                )
            else:
                prediction['reason'] = (
                    'Attendance is close to the target threshold.'
                )
                prediction['recommendation'] = (
                    'Keep a steady attendance routine to stay on track.'
                )

        high_risk = [
            prediction
            for prediction in predictions
            if prediction['risk_level'] == 'High'
        ]

        completed_classes = sum(
            1
            for record in today_records
            if record['status'] == 'Present'
        )

        remaining_classes = max(
            0,
            len(todays_classes) - completed_classes
        )
        reminder_settings = Student.email_reminder_settings(current_user.id)

        return render_template(
            'dashboard.html',
            greeting=greeting,
            todays_classes=todays_classes,
            overall_percentage=overall_pct,
            recent_attendance=recent_attendance,
            predictions=predictions,
            high_risk=high_risk,
            today=today_date.strftime('%A, %B %d, %Y'),
            today_short=today_date.strftime('%d %b %Y'),
            subject_cards=subject_cards,
            attendance_warning=attendance_warning,
            target_percentage=attendance_target,
            required_classes=required_classes,
            streak=streak,
            next_class=next_class,
            attendance_present=total_present,
            attendance_total=total_records,
            completed_classes=completed_classes,
            remaining_classes=remaining_classes,
            email_reminders_enabled=reminder_settings['enabled'],
            reminder_minutes_before=reminder_settings['reminder_minutes']
        )

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=Config.DEBUG)
