from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from config import Config
from models.schedule import Schedule


def get_upcoming_classes(student_id):
    now = datetime.now(ZoneInfo(Config.REMINDER_TIMEZONE))
    today_name = now.strftime('%A')
    classes_today = Schedule.get_today(student_id, today_name)

    upcoming = []
    for class_row in classes_today:
        try:
            start_time = datetime.strptime(
                class_row['start_time'],
                '%H:%M'
            ).time()
        except (TypeError, ValueError):
            logging.getLogger(__name__).warning(
                'Skipping invalid timetable reminder: schedule_id=%s',
                class_row['id']
            )
            continue
        start_datetime = datetime.combine(
            now.date(),
            start_time,
            tzinfo=now.tzinfo
        )
        minutes_until = (start_datetime - now).total_seconds() / 60

        if 0 <= minutes_until <= Config.REMINDER_WINDOW_MINUTES:
            upcoming.append({
                'subject': class_row['subject'],
                'room': class_row['room'],
                'faculty': class_row['faculty'],
                'start_time': class_row['start_time'],
                'minutes_until': round(minutes_until)
            })

    return upcoming
