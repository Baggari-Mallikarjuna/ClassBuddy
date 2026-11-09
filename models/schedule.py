from utils.database import managed_connection


def _time_to_minutes(value):
    try:
        hour, minute = map(int, value.split(':'))
        return hour * 60 + minute
    except (TypeError, ValueError):
        return 0


class Schedule:
    @staticmethod
    def add(student_id, subject, faculty, room, day, start_time, end_time):
        with managed_connection() as conn:
            cursor = conn.execute(
                '''INSERT INTO schedule
                   (student_id, subject, faculty, room, day, start_time, end_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (student_id, subject, faculty, room, day, start_time, end_time)
            )
        schedule_id = cursor.lastrowid
        return schedule_id

    @staticmethod
    def update(student_id, schedule_id, subject, faculty, room, day, start_time, end_time):
        with managed_connection() as conn:
            cursor = conn.execute(
                '''UPDATE schedule SET subject = ?, faculty = ?, room = ?, day = ?,
                   start_time = ?, end_time = ?
                   WHERE id = ? AND student_id = ?''',
                (
                    subject, faculty, room, day, start_time, end_time,
                    schedule_id, student_id
                )
            )
        return cursor.rowcount > 0

    @staticmethod
    def delete(student_id, schedule_id):
        with managed_connection() as conn:
            cursor = conn.execute(
                'DELETE FROM schedule WHERE id = ? AND student_id = ?',
                (schedule_id, student_id)
            )
        return cursor.rowcount > 0

    @staticmethod
    def has_time_conflict(
        student_id,
        day,
        start_time,
        end_time,
        exclude_id=None
    ):
        with managed_connection() as conn:
            if exclude_id is None:
                rows = conn.execute(
                    'SELECT * FROM schedule WHERE student_id = ? AND day = ?',
                    (student_id, day)
                ).fetchall()
            else:
                rows = conn.execute(
                    '''SELECT * FROM schedule
                       WHERE student_id = ? AND day = ? AND id != ?''',
                    (student_id, day, exclude_id)
                ).fetchall()

        start_minutes = _time_to_minutes(start_time)
        end_minutes = _time_to_minutes(end_time)
        for row in rows:
            row_start = _time_to_minutes(row['start_time'])
            row_end = _time_to_minutes(row['end_time'])
            if start_minutes < row_end and end_minutes > row_start:
                return row
        return None

    @staticmethod
    def get_all(student_id):
        with managed_connection() as conn:
            rows = conn.execute(
                '''SELECT * FROM schedule WHERE student_id = ?
                   ORDER BY day, start_time''',
                (student_id,)
            ).fetchall()
        return rows

    @staticmethod
    def get_by_id(student_id, schedule_id):
        with managed_connection() as conn:
            row = conn.execute(
                'SELECT * FROM schedule WHERE id = ? AND student_id = ?',
                (schedule_id, student_id)
            ).fetchone()
        return row

    @staticmethod
    def get_today(student_id, day_name):
        with managed_connection() as conn:
            rows = conn.execute(
                '''SELECT * FROM schedule
                   WHERE student_id = ? AND day = ? ORDER BY start_time''',
                (student_id, day_name)
            ).fetchall()
        return rows

    @staticmethod
    def get_distinct_subjects(student_id):
        with managed_connection() as conn:
            rows = conn.execute(
                '''SELECT DISTINCT subject FROM schedule
                   WHERE student_id = ? ORDER BY subject''',
                (student_id,)
            ).fetchall()
        return [row['subject'] for row in rows]
