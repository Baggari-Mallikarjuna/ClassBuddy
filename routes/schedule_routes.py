from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models.schedule import Schedule

schedule_bp = Blueprint('schedule', __name__)

DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def _normalize_selected_day(day_value):
    selected_day = (day_value or '').strip()
    if selected_day in DAYS_OF_WEEK:
        return selected_day
    return datetime.now().strftime('%A')


def _time_to_minutes(value):
    try:
        hour, minute = map(int, value.split(':'))
        return hour * 60 + minute
    except (TypeError, ValueError):
        return 0


def _class_status(class_row):
    now = datetime.now()
    try:
        start_dt = datetime.combine(date.today(), datetime.strptime(class_row['start_time'], '%H:%M').time())
        end_dt = datetime.combine(date.today(), datetime.strptime(class_row['end_time'], '%H:%M').time())
    except ValueError:
        return {'label': 'Upcoming', 'class_name': 'upcoming'}

    if start_dt <= now <= end_dt:
        return {'label': 'Current', 'class_name': 'current'}
    if now < start_dt:
        return {'label': 'Upcoming', 'class_name': 'upcoming'}
    return {'label': 'Completed', 'class_name': 'completed'}


@schedule_bp.route('/schedule')
@login_required
def view_schedule():
    selected_day = _normalize_selected_day(request.args.get('day', ''))
    classes = Schedule.get_all(current_user.id)
    grouped = {day: [] for day in DAYS_OF_WEEK}

    for class_row in classes:
        record = dict(class_row)
        if record['day'] in grouped:
            grouped[record['day']].append(record)

    for day in DAYS_OF_WEEK:
        grouped[day].sort(key=lambda row: _time_to_minutes(row['start_time']))
        for class_row in grouped[day]:
            status = _class_status(class_row)
            class_row['status_label'] = status['label']
            class_row['status_class'] = status['class_name']

    today_name = datetime.now().strftime('%A')
    selected_day_classes = grouped.get(selected_day, [])
    return render_template(
        'schedule.html',
        grouped_schedule=grouped,
        selected_day_classes=selected_day_classes,
        days=DAYS_OF_WEEK,
        selected_day=selected_day,
        today_name=today_name,
        current_day=today_name,
        day_counts={day: len(grouped[day]) for day in DAYS_OF_WEEK},
    )


@schedule_bp.route('/schedule/add', methods=['POST'])
@login_required
def add_class():
    subject = request.form.get('subject', '').strip()
    faculty = request.form.get('faculty', '').strip()
    room = request.form.get('room', '').strip()
    day = request.form.get('day', '').strip()
    start_time = request.form.get('start_time', '').strip()
    end_time = request.form.get('end_time', '').strip()
    selected_day = _normalize_selected_day(request.form.get('selected_day', day))

    if not all([subject, faculty, room, day, start_time, end_time]):
        flash('All fields are required.', 'danger')
        return redirect(url_for('schedule.view_schedule', day=selected_day))

    if day not in DAYS_OF_WEEK:
        flash('Please choose a valid day.', 'warning')
        return redirect(url_for('schedule.view_schedule', day=selected_day))

    if Schedule.has_time_conflict(
        current_user.id,
        day,
        start_time,
        end_time
    ):
        flash('⚠ Time Conflict. Another class is already scheduled during this time.', 'warning')
        return redirect(url_for('schedule.view_schedule', day=day))

    Schedule.add(
        current_user.id,
        subject,
        faculty,
        room,
        day,
        start_time,
        end_time
    )
    flash(f'{subject} added to timetable.', 'success')
    return redirect(url_for('schedule.view_schedule', day=day))


@schedule_bp.route('/schedule/edit/<int:schedule_id>', methods=['POST'])
@login_required
def edit_class(schedule_id):
    subject = request.form.get('subject', '').strip()
    faculty = request.form.get('faculty', '').strip()
    room = request.form.get('room', '').strip()
    day = request.form.get('day', '').strip()
    start_time = request.form.get('start_time', '').strip()
    end_time = request.form.get('end_time', '').strip()
    selected_day = _normalize_selected_day(request.form.get('selected_day', day))

    existing = Schedule.get_by_id(current_user.id, schedule_id)
    if not existing:
        flash('Class not found.', 'danger')
        return redirect(url_for('schedule.view_schedule', day=selected_day))

    if not all([subject, faculty, room, day, start_time, end_time]):
        flash('All fields are required.', 'danger')
        return redirect(url_for('schedule.view_schedule', day=selected_day))

    if Schedule.has_time_conflict(
        current_user.id,
        day,
        start_time,
        end_time,
        exclude_id=schedule_id
    ):
        flash('⚠ Time Conflict. Another class is already scheduled during this time.', 'warning')
        return redirect(url_for('schedule.view_schedule', day=day))

    Schedule.update(
        current_user.id,
        schedule_id,
        subject,
        faculty,
        room,
        day,
        start_time,
        end_time
    )
    flash('Class updated.', 'success')
    return redirect(url_for('schedule.view_schedule', day=day))


@schedule_bp.route('/schedule/delete/<int:schedule_id>', methods=['POST'])
@login_required
def delete_class(schedule_id):
    selected_day = _normalize_selected_day(request.form.get('day') or request.args.get('day', ''))
    if not Schedule.delete(current_user.id, schedule_id):
        flash('Class not found.', 'danger')
        return redirect(url_for('schedule.view_schedule', day=selected_day))
    flash('Class removed from timetable.', 'info')
    return redirect(url_for('schedule.view_schedule', day=selected_day))
