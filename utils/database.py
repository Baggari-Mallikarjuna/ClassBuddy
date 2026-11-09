import contextlib
import os
import sqlite3
import tempfile
import threading

from dotenv import load_dotenv
from utils.process_lock import acquire_process_lock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(
    dotenv_path=os.path.join(BASE_DIR, '.env'),
    override=False
)

LEGACY_DATABASE_PATH = os.path.join(
    BASE_DIR,
    'database',
    'classbuddy.db'
)


def _default_database_path():
    data_root = (
        os.environ.get('LOCALAPPDATA')
        or os.environ.get('XDG_DATA_HOME')
        or os.path.join(os.path.expanduser('~'), '.local', 'share')
    )
    return os.path.abspath(
        os.path.join(data_root, 'ClassBuddy', 'classbuddy.db')
    )


DEFAULT_DATABASE_PATH = _default_database_path()
DATABASE_PATH = os.path.abspath(
    os.environ.get('CLASSBUDDY_DATABASE', DEFAULT_DATABASE_PATH)
)
SQLITE_TIMEOUT_SECONDS = 15
SQLITE_BUSY_TIMEOUT_MS = SQLITE_TIMEOUT_SECONDS * 1000
DATABASE_INIT_LOCK_TIMEOUT_SECONDS = 30
_DATABASE_INIT_LOCK = threading.Lock()

_TABLE_STATEMENTS = {
    'students': '''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''',
    'schedule': '''
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            faculty TEXT NOT NULL,
            room TEXT NOT NULL,
            day TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            student_id INTEGER REFERENCES students (id)
        )
    ''',
    'attendance': '''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            date TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    ''',
    'predictions': '''
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            probability REAL NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    ''',
    'email_reminder_history': '''
        CREATE TABLE IF NOT EXISTS email_reminder_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            schedule_id INTEGER NOT NULL,
            class_start TEXT NOT NULL,
            status TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            error_type TEXT,
            UNIQUE (student_id, schedule_id, class_start)
        )
    ''',
    'student_notification_settings': '''
        CREATE TABLE IF NOT EXISTS student_notification_settings (
            student_id INTEGER PRIMARY KEY
                REFERENCES students (id) ON DELETE CASCADE,
            email_reminders_enabled INTEGER NOT NULL DEFAULT 1
                CHECK (email_reminders_enabled IN (0, 1)),
            reminder_minutes INTEGER NOT NULL DEFAULT 15
                CHECK (reminder_minutes IN (5, 10, 15, 20, 30)),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''',
}


def _is_onedrive_path(path):
    normalized_path = os.path.normcase(os.path.abspath(path))
    path_parts = normalized_path.split(os.sep)
    if any(part.lower().startswith('onedrive') for part in path_parts):
        return True

    for variable in ('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer'):
        root = os.environ.get(variable)
        if not root:
            continue
        try:
            if os.path.commonpath((
                normalized_path,
                os.path.normcase(os.path.abspath(root))
            )) == os.path.normcase(os.path.abspath(root)):
                return True
        except ValueError:
            continue
    return False


def get_db_connection():
    conn = sqlite3.connect(
        DATABASE_PATH,
        timeout=SQLITE_TIMEOUT_SECONDS
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f'PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}')
        conn.execute('PRAGMA foreign_keys = ON')
        return conn
    except BaseException:
        conn.close()
        raise


@contextlib.contextmanager
def managed_connection():
    conn = get_db_connection()
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        try:
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()


def _migrate_legacy_database():
    if (
        os.path.normcase(DATABASE_PATH)
        != os.path.normcase(DEFAULT_DATABASE_PATH)
        or os.path.exists(DATABASE_PATH)
        or not os.path.isfile(LEGACY_DATABASE_PATH)
        or os.path.normcase(LEGACY_DATABASE_PATH)
        == os.path.normcase(DATABASE_PATH)
    ):
        return

    database_directory = os.path.dirname(DATABASE_PATH)
    os.makedirs(database_directory, exist_ok=True)
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix='.classbuddy-migration-',
        suffix='.db',
        dir=database_directory
    )
    os.close(file_descriptor)

    try:
        with contextlib.ExitStack() as connections:
            source = sqlite3.connect(
                LEGACY_DATABASE_PATH,
                timeout=SQLITE_TIMEOUT_SECONDS
            )
            connections.callback(source.close)
            destination = sqlite3.connect(
                temporary_path,
                timeout=SQLITE_TIMEOUT_SECONDS
            )
            connections.callback(destination.close)
            source.execute(
                f'PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}'
            )
            destination.execute(
                f'PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}'
            )
            source.backup(destination)

        if os.path.exists(DATABASE_PATH):
            raise FileExistsError(
                f'The new ClassBuddy database appeared during migration: '
                f'{DATABASE_PATH!r}'
            )
        os.replace(temporary_path, DATABASE_PATH)
    finally:
        for suffix in ('', '-journal', '-wal', '-shm'):
            temporary_file = temporary_path + suffix
            if os.path.exists(temporary_file):
                os.remove(temporary_file)


def init_db():
    if not _DATABASE_INIT_LOCK.acquire(
        timeout=DATABASE_INIT_LOCK_TIMEOUT_SECONDS
    ):
        raise TimeoutError(
            'Timed out waiting for another thread to initialize the '
            'ClassBuddy database.'
        )

    conn = None
    process_lock = None
    try:
        process_lock = acquire_process_lock(
            'classbuddy-database-init',
            DATABASE_PATH,
            timeout=DATABASE_INIT_LOCK_TIMEOUT_SECONDS
        )
        _migrate_legacy_database()
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
        conn = get_db_connection()

        wal_requested = os.environ.get(
            'CLASSBUDDY_SQLITE_WAL',
            ''
        ).strip().lower() in {
            '1', 'true', 'yes', 'on'
        }
        onedrive_path = _is_onedrive_path(DATABASE_PATH)
        current_journal_mode = conn.execute(
            'PRAGMA journal_mode'
        ).fetchone()[0].lower()
        if onedrive_path and (
            wal_requested or current_journal_mode == 'wal'
        ):
            raise RuntimeError(
                'SQLite WAL mode is not supported for a database stored '
                'inside a OneDrive-synchronized directory. Move the '
                'database to local, non-synchronized storage first.'
            )
        if wal_requested and current_journal_mode != 'wal':
            journal_mode = conn.execute(
                'PRAGMA journal_mode = WAL'
            ).fetchone()[0]
            if journal_mode.lower() != 'wal':
                raise sqlite3.OperationalError(
                    f'Could not enable SQLite WAL mode (received '
                    f'{journal_mode!r}).'
                )

        existing_tables = {
            row['name']
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing_tables = set(_TABLE_STATEMENTS) - existing_tables

        schedule_columns = set()
        if 'schedule' in existing_tables:
            schedule_columns = {
                row['name']
                for row in conn.execute(
                    'PRAGMA table_info(schedule)'
                ).fetchall()
            }
        needs_student_id = (
            'schedule' in existing_tables
            and 'student_id' not in schedule_columns
        )
        notification_columns = set()
        if 'student_notification_settings' in existing_tables:
            notification_columns = {
                row['name']
                for row in conn.execute(
                    'PRAGMA table_info(student_notification_settings)'
                ).fetchall()
            }
        needs_notification_migration = (
            'student_notification_settings' in existing_tables
            and not {
                'reminder_minutes',
                'updated_at',
            }.issubset(notification_columns)
        )
        schedule_index_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_schedule_student_day_start'"
        ).fetchone() is not None
        if (
            not missing_tables
            and not needs_student_id
            and not needs_notification_migration
            and schedule_index_exists
        ):
            return

        conn.execute('BEGIN IMMEDIATE')
        for statement in _TABLE_STATEMENTS.values():
            conn.execute(statement)

        if needs_student_id:
            conn.execute(
                'ALTER TABLE schedule ADD COLUMN student_id '
                'INTEGER REFERENCES students (id)'
            )

        if (
            'student_notification_settings' in existing_tables
            and 'reminder_minutes' not in notification_columns
        ):
            conn.execute(
                '''ALTER TABLE student_notification_settings
                   ADD COLUMN reminder_minutes INTEGER NOT NULL DEFAULT 15
                   CHECK (reminder_minutes IN (5, 10, 15, 20, 30))'''
            )
        if (
            'student_notification_settings' in existing_tables
            and 'updated_at' not in notification_columns
        ):
            conn.execute(
                'ALTER TABLE student_notification_settings '
                'ADD COLUMN updated_at TEXT'
            )
        conn.execute(
            '''UPDATE student_notification_settings
               SET updated_at = CURRENT_TIMESTAMP
               WHERE updated_at IS NULL OR updated_at = '' '''
        )
        conn.execute(
            '''INSERT OR IGNORE INTO student_notification_settings
               (student_id, email_reminders_enabled, reminder_minutes,
                updated_at)
               SELECT id, 1, 15, CURRENT_TIMESTAMP FROM students'''
        )
        conn.execute(
            '''CREATE INDEX IF NOT EXISTS idx_schedule_student_day_start
               ON schedule (student_id, day, start_time)'''
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        if conn is not None:
            conn.rollback()
        if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
            raise sqlite3.OperationalError(
                f'SQLite could not initialize {DATABASE_PATH!r} because '
                'another process or file-sync operation is holding a lock. '
                'Stop other ClassBuddy instances and move the database out '
                'of OneDrive if the problem persists.'
            ) from exc
        raise
    except BaseException:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()
        if process_lock is not None:
            process_lock.close()
        _DATABASE_INIT_LOCK.release()
