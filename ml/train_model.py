import os
import sys
import numpy as np
import joblib
from sklearn.linear_model import LogisticRegression

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config


def generate_training_data(samples=800):
    rng = np.random.default_rng(42)
    attendance_pct = rng.uniform(0, 1, samples)
    absent_streak = rng.integers(0, 8, samples)
    recent_pct = np.clip(attendance_pct + rng.normal(0, 0.1, samples), 0, 1)
    total_classes = rng.integers(5, 60, samples)

    risk_score = (1 - attendance_pct) * 0.5 + (absent_streak / 8) * 0.35 + (1 - recent_pct) * 0.15
    noise = rng.normal(0, 0.05, samples)
    labels = (risk_score + noise > 0.45).astype(int)

    features = np.column_stack([attendance_pct, absent_streak, recent_pct, total_classes])
    return features, labels


def train_and_save():
    features, labels = generate_training_data()
    model = LogisticRegression(max_iter=1000)
    model.fit(features, labels)

    accuracy = model.score(features, labels)
    print(f'Training accuracy: {accuracy:.2%}')

    os.makedirs(os.path.dirname(Config.ML_MODEL_PATH), exist_ok=True)
    joblib.dump(model, Config.ML_MODEL_PATH)
    print(f'Model saved to {Config.ML_MODEL_PATH}')


if __name__ == '__main__':
    train_and_save()
