from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    request,
    url_for,
)
from flask_login import current_user, login_required
from models.student import REMINDER_MINUTE_OPTIONS, Student
from services.email_service import (
    EmailDeliveryError,
    EmailConfigurationError,
    is_valid_email,
    send_test_email,
)
from services.reminder_service import get_upcoming_classes

reminder_bp = Blueprint('reminder', __name__)


@reminder_bp.route('/settings/email-reminders', methods=['POST'])
@login_required
def update_email_reminder_settings():
    preference = request.form.get('email_reminders_enabled')
    if preference not in {'enabled', 'disabled'}:
        flash('Choose whether to enable or disable email reminders.', 'danger')
        return redirect(url_for('settings_page'))

    reminder_value = request.form.get('reminder_minutes')
    if reminder_value is None:
        reminder_minutes = Student.email_reminder_settings(
            current_user.id
        )['reminder_minutes']
    elif reminder_value in {str(value) for value in REMINDER_MINUTE_OPTIONS}:
        reminder_minutes = int(reminder_value)
    else:
        flash('Choose a valid reminder time.', 'danger')
        return redirect(url_for('settings_page'))

    enabled = preference == 'enabled'
    Student.set_email_reminder_settings(
        current_user.id,
        enabled,
        reminder_minutes
    )
    if enabled:
        flash(
            f'Settings saved successfully! Your class reminders will be '
            f'sent to {current_user.email}, {reminder_minutes} minutes '
            'before each class.',
            'success'
        )
    else:
        flash(
            'Settings saved successfully! Email reminders disabled.',
            'success'
        )
    return redirect(url_for('settings_page'))


@reminder_bp.route('/api/reminders/upcoming')
@login_required
def upcoming_reminders():
    upcoming = get_upcoming_classes(current_user.id)
    return jsonify({'upcoming': upcoming})


@reminder_bp.route('/settings/test-email', methods=['POST'])
@login_required
def send_test_email_route():
    try:
        if not is_valid_email(current_user.email):
            flash('Your profile does not have a valid email address.', 'danger')
            return redirect(url_for('settings_page'))
        send_test_email(
            current_app.config,
            current_user.email,
            current_user.name
        )
    except EmailConfigurationError as exc:
        current_app.logger.error(
            'Test email configuration failure: student_id=%s error_type=%s '
            'diagnostic=%s',
            current_user.id,
            type(exc).__name__,
            str(exc)
        )
        flash(
            f'{exc} Add the required SMTP settings to the project .env file '
            'and restart ClassBuddy.',
            'danger'
        )
    except EmailDeliveryError as exc:
        current_app.logger.error(
            'Test email failed: student_id=%s stage=%s exception_type=%s '
            'provider_status=%s diagnostic=%s',
            current_user.id,
            exc.stage,
            exc.exception_type,
            exc.provider_status,
            exc.diagnostic
        )
        flash(
            f'Test email failed during {exc.stage.lower()}: '
            f'{exc.diagnostic}',
            'danger'
        )
    else:
        current_app.logger.info(
            'Test email sent: student_id=%s',
            current_user.id
        )
        flash('Test email sent to your registered email address.', 'success')

    return redirect(url_for('settings_page'))
