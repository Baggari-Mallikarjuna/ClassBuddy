import os
import joblib
from config import Config
from models.attendance import Attendance
from models.schedule import Schedule
from models.predictor import Prediction


def _load_model():
    if not os.path.exists(Config.ML_MODEL_PATH):
        raise FileNotFoundError('ML model not found. Run ml/train_model.py first.')
    return joblib.load(Config.ML_MODEL_PATH)


def _build_features(student_id, subject):
    records = Attendance.get_by_subject(student_id, subject)
    total = len(records)
    if total == 0:
        return None

    present = sum(1 for r in records if r['status'] == 'Present')
    attendance_pct = present / total

    absent_streak = 0
    for record in reversed(records):
        if record['status'] == 'Absent':
            absent_streak += 1
        else:
            break

    recent = records[-5:]
    recent_present = sum(1 for r in recent if r['status'] == 'Present')
    recent_pct = recent_present / len(recent) if recent else attendance_pct

    return [attendance_pct, absent_streak, recent_pct, total]


def predict_risk_for_student(student_id):
    model = _load_model()
    subjects = Schedule.get_distinct_subjects(student_id)
    results = []

    for subject in subjects:
        features = _build_features(student_id, subject)
        if features is None:
            continue

        probability = model.predict_proba([features])[0][1]

        if probability < 0.33:
            risk_level = 'Low'
        elif probability < 0.66:
            risk_level = 'Medium'
        else:
            risk_level = 'High'

        Prediction.save(student_id, subject, risk_level, round(float(probability), 3))
        results.append({
            'subject': subject,
            'risk_level': risk_level,
            'probability': round(float(probability) * 100, 1)
        })

    return results
