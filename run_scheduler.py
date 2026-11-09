import os
import time

# The scheduler process starts its scheduler explicitly after acquiring a lock.
os.environ['REMINDER_SCHEDULER_ENABLED'] = 'false'


def main():
    from app import app
    from services.reminder_scheduler import (
        SCHEDULER_EXTENSION_KEY,
        start_reminder_scheduler,
    )

    app.config.update({
        'DEBUG': False,
        'REMINDER_SCHEDULER_ENABLED': True
    })
    scheduler = start_reminder_scheduler(app)
    if scheduler is None:
        raise RuntimeError('The ClassBuddy reminder scheduler did not start.')

    app.extensions[SCHEDULER_EXTENSION_KEY] = scheduler
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)


if __name__ == '__main__':
    main()
