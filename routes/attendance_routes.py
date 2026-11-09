import csv
import io
from datetime import date

from flask import Blueprint, flash, jsonify, redirect, render_template, request, Response, url_for
from flask_login import current_user, login_required

from models.attendance import Attendance
from models.schedule import Schedule

attendance_bp = Blueprint('attendance', __name__)


@attendance_bp.route('/attendance')
@login_required
def view_attendance():
    subjects = Schedule.get_distinct_subjects(current_user.id)
    all_history = Attendance.get_history(current_user.id, limit=500)
    subject_filter = request.args.get('subject', '').strip()
    status_filter = request.args.get('status', '').strip()
    date_filter = request.args.get('date', '').strip()
    return_day = request.args.get('return_day', '').strip()

    history = list(all_history)
    if subject_filter:
        history = [record for record in history if record['subject'] == subject_filter]
    if status_filter:
        history = [record for record in history if record['status'] == status_filter]
    if date_filter:
        history = [record for record in history if record['date'] == date_filter]

    total_records = len(history)
    present_count = sum(1 for record in history if record['status'] == 'Present')
    absent_count = total_records - present_count
    average_pct = round((present_count / total_records) * 100, 1) if total_records else 0

    return render_template(
        'attendance.html',
        subjects=subjects,
        history=history,
        all_history=all_history,
        today=date.today().isoformat(),
        return_day=return_day,
        summary={
            'total_records': total_records,
            'present_count': present_count,
            'absent_count': absent_count,
            'average_pct': average_pct,
        },
        filters={
            'subject': subject_filter,
            'status': status_filter,
            'date': date_filter,
        }
    )


@attendance_bp.route('/attendance/mark', methods=['POST'])
@login_required
def mark_attendance():
    subject = request.form.get('subject', '')
    status = request.form.get('status', '')
    entry_date = request.form.get('date') or date.today().isoformat()
    return_day = request.form.get('return_day', '').strip()

    if (
        subject not in Schedule.get_distinct_subjects(current_user.id)
        or status not in ('Present', 'Absent')
    ):
        flash('Invalid attendance entry.', 'danger')
        if return_day:
            return redirect(url_for('schedule.view_schedule', day=return_day))
        return redirect(url_for('attendance.view_attendance'))

    Attendance.mark(current_user.id, subject, entry_date, status)
    flash(f'Marked {status} for {subject} on {entry_date}.', 'success')
    if return_day:
        return redirect(url_for('schedule.view_schedule', day=return_day))
    return redirect(url_for('attendance.view_attendance'))


@attendance_bp.route('/attendance/export')
@login_required
def export_attendance_csv():
    history = Attendance.get_history(current_user.id, limit=5000)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Date', 'Subject', 'Status'])
    for record in history:
        writer.writerow([record['date'], record['subject'], record['status']])

    csv_data = buffer.getvalue()
    response = Response(csv_data, mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=classbuddy_attendance_report.csv'
    return response


@attendance_bp.route('/analytics')
@login_required
def view_analytics():
    return render_template('analytics.html')


@attendance_bp.route('/api/analytics/data')
@login_required
def analytics_data():
    overall_pct = Attendance.get_overall_percentage(current_user.id)
    subject_wise = Attendance.get_subject_wise_percentage(current_user.id)
    monthly = Attendance.get_monthly_summary(current_user.id)
    history = Attendance.get_history(current_user.id, limit=500)
    present_count = sum(1 for record in history if record['status'] == 'Present')
    absent_count = len(history) - present_count

    return jsonify({
        'overall_percentage': overall_pct,
        'subject_wise': subject_wise,
        'monthly': monthly,
        'present_classes': present_count,
        'absent_classes': absent_count,
        'subjects_at_risk': sum(1 for item in subject_wise if item['percentage'] < 75),
        'summary': {
            'present_classes': present_count,
            'absent_classes': absent_count,
            'subjects_at_risk': sum(1 for item in subject_wise if item['percentage'] < 75),
        }
    })
