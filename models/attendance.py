from utils.database import managed_connection


class Attendance:
    @staticmethod
    def mark(student_id, subject, date, status):
        with managed_connection() as conn:
            existing = conn.execute(
                'SELECT id FROM attendance WHERE student_id = ? '
                'AND subject = ? AND date = ?',
                (student_id, subject, date)
            ).fetchone()

            if existing:
                conn.execute(
                    'UPDATE attendance SET status = ? WHERE id = ?',
                    (status, existing['id'])
                )
            else:
                conn.execute(
                    'INSERT INTO attendance '
                    '(student_id, subject, date, status) VALUES (?, ?, ?, ?)',
                    (student_id, subject, date, status)
                )

    @staticmethod
    def get_history(student_id, limit=20):
        with managed_connection() as conn:
            rows = conn.execute(
                'SELECT * FROM attendance WHERE student_id = ? '
                'ORDER BY date DESC LIMIT ?',
                (student_id, limit)
            ).fetchall()
        return rows

    @staticmethod
    def get_by_subject(student_id, subject):
        with managed_connection() as conn:
            rows = conn.execute(
                'SELECT * FROM attendance WHERE student_id = ? AND subject = ? '
                'ORDER BY date',
                (student_id, subject)
            ).fetchall()
        return rows

    @staticmethod
    def get_overall_percentage(student_id):
        with managed_connection() as conn:
            total = conn.execute(
                'SELECT COUNT(*) as cnt FROM attendance WHERE student_id = ?',
                (student_id,)
            ).fetchone()['cnt']
            present = conn.execute(
                "SELECT COUNT(*) as cnt FROM attendance "
                "WHERE student_id = ? AND status = 'Present'",
                (student_id,)
            ).fetchone()['cnt']
        if total == 0:
            return 0
        return round((present / total) * 100, 1)

    @staticmethod
    def get_subject_wise_percentage(student_id):
        with managed_connection() as conn:
            rows = conn.execute(
                '''SELECT subject,
                          COUNT(*) as total,
                          SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) as present
                   FROM attendance WHERE student_id = ? GROUP BY subject''',
                (student_id,)
            ).fetchall()

        result = []
        for row in rows:
            pct = round((row['present'] / row['total']) * 100, 1) if row['total'] else 0
            result.append({'subject': row['subject'], 'percentage': pct, 'total': row['total']})
        return result

    @staticmethod
    def get_monthly_summary(student_id):
        with managed_connection() as conn:
            rows = conn.execute(
                '''SELECT strftime('%Y-%m', date) as month,
                          COUNT(*) as total,
                          SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) as present
                   FROM attendance WHERE student_id = ? GROUP BY month ORDER BY month''',
                (student_id,)
            ).fetchall()

        result = []
        for row in rows:
            pct = round((row['present'] / row['total']) * 100, 1) if row['total'] else 0
            result.append({'month': row['month'], 'percentage': pct})
        return result
