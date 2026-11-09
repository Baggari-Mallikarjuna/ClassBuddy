import os
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_FILE = os.path.join(BASE_DIR, '.env')


def load_project_environment():
    return load_dotenv(dotenv_path=ENV_FILE, override=False)


load_project_environment()

from utils.database import DATABASE_PATH


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'classbuddy-dev-secret-key-change-in-production')
    DATABASE = DATABASE_PATH
    ML_MODEL_PATH = os.path.join(BASE_DIR, 'ml', 'attendance_model.pkl')
    REMINDER_WINDOW_MINUTES = 10
    REMINDER_MINUTES_BEFORE = int(
        os.environ.get('REMINDER_MINUTES_BEFORE', '15')
    )
    REMINDER_POLL_INTERVAL_SECONDS = 30
    REMINDER_TIMEZONE = 'Asia/Kolkata'
    REMINDER_SCHEDULER_ENABLED = _env_bool(
        'REMINDER_SCHEDULER_ENABLED',
        False
    )
    DEBUG = _env_bool('FLASK_DEBUG', True)

    MAIL_SERVER = os.environ.get('MAIL_SERVER', '')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', '587'))
    MAIL_USE_TLS = _env_bool('MAIL_USE_TLS', True)
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', '')
    MAIL_SENDER_NAME = os.environ.get('MAIL_SENDER_NAME', 'ClassBuddy')
    MAIL_TIMEOUT = int(os.environ.get('MAIL_TIMEOUT', '15'))
