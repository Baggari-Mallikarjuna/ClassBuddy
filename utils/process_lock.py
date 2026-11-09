import hashlib
import os
import tempfile
import time


def acquire_process_lock(name, resource, timeout=0):
    resource_key = os.path.normcase(os.path.realpath(resource))
    lock_key = hashlib.sha256(
        f'{name}:{resource_key}'.encode('utf-8')
    ).hexdigest()
    lock_path = os.path.join(
        tempfile.gettempdir(),
        f'{name}-{lock_key}.lock'
    )
    lock_file = open(lock_path, 'a+b')
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b'\0')
        lock_file.flush()
    lock_file.seek(0)

    deadline = time.monotonic() + timeout
    while True:
        try:
            if os.name == 'nt':
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            return lock_file
        except OSError as exc:
            if time.monotonic() >= deadline:
                lock_file.close()
                raise RuntimeError(
                    f'Another {name} process is already running for this '
                    'resource.'
                ) from exc
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
