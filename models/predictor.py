from utils.database import managed_connection


class Prediction:
    @staticmethod
    def save(student_id, subject, risk_level, probability):
        with managed_connection() as conn:
            existing = conn.execute(
                'SELECT id FROM predictions WHERE student_id = ? AND subject = ?',
                (student_id, subject)
            ).fetchone()

            if existing:
                conn.execute(
                    'UPDATE predictions SET risk_level = ?, probability = ? '
                    'WHERE id = ?',
                    (risk_level, probability, existing['id'])
                )
            else:
                conn.execute(
                    '''INSERT INTO predictions (student_id, subject, risk_level, probability)
                       VALUES (?, ?, ?, ?)''',
                    (student_id, subject, risk_level, probability)
                )

    @staticmethod
    def get_for_student(student_id):
        with managed_connection() as conn:
            rows = conn.execute(
                'SELECT * FROM predictions WHERE student_id = ? '
                'ORDER BY probability DESC',
                (student_id,)
            ).fetchall()
        return rows

    @staticmethod
    def get_high_risk(student_id):
        with managed_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM predictions WHERE student_id = ? "
                "AND risk_level = 'High' ORDER BY probability DESC",
                (student_id,)
            ).fetchall()
        return rows
