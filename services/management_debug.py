import json
import os
import threading
from datetime import datetime


_LOCK = threading.Lock()


def log(event, **data):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "management_context_debug.log")

    record = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "event": str(event),
        **data,
    }

    line = json.dumps(record, ensure_ascii=False, default=str)
    with _LOCK:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
