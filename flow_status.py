import threading
from datetime import datetime


class FlowStatus:
    def __init__(self):
        self._lock = threading.RLock()
        self.running = False
        self.healthy = False
        self.last_scan = None
        self.last_error = None
        self.error_count = 0
        self.nodes = {}
        self.errors = []

    def start(self):
        with self._lock:
            self.running = True
            self.healthy = True
            self.last_error = None
            self.error_count = 0
            self.errors.clear()

    def stop(self):
        with self._lock:
            self.running = False
            self.healthy = False

    def update_scan(self):
        with self._lock:
            self.last_scan = datetime.now()

    def node_ok(self, node_id):
        with self._lock:
            self.nodes[node_id] = {
                "status": "OK",
                "time": datetime.now(),
            }

    def node_error(self, node_id, error):
        with self._lock:
            message = str(error)
            now = datetime.now()
            diagnostic = {
                "node": node_id,
                "error": message,
                "time": now,
            }
            self.nodes[node_id] = {
                "status": "ERROR",
                "time": now,
                "error": message,
            }
            self.last_error = diagnostic
            self.error_count += 1
            self.errors.append(dict(diagnostic))
            if len(self.errors) > 200:
                del self.errors[:-200]
            self.healthy = False

    def clear_health_error(self):
        with self._lock:
            self.last_error = None
            self.healthy = bool(self.running)

    def get_status(self):
        with self._lock:
            return {
                "running": self.running,
                "healthy": self.healthy,
                "last_scan": str(self.last_scan) if self.last_scan else None,
                "last_error": self.last_error,
                "error_count": self.error_count,
                "nodes": dict(self.nodes),
                "errors": list(self.errors),
            }


flow_status = FlowStatus()
