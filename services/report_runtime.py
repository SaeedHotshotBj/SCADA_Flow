"""Report runtime lifecycle.

Production report rows are created by EdgeTriggerService when a Flow-defined
Rise or Fall production event occurs. This worker remains as the explicit
bootstrap-compatible service, but it no longer creates periodic TIME snapshots
that could mix historian sampling with production records.
"""

import threading

_START_LOCK = threading.Lock()
_WORKER = None


class ReportRuntime:
    def __init__(self):
        self.running = True

    def run(self):
        # Intentionally idle. TIME data remains in PLC_Data/TagHistory for
        # Trend and trace queries; ReportOutput persistence is event-driven.
        return None

    def stop(self):
        self.running = False


def start():
    global _WORKER
    with _START_LOCK:
        if _WORKER is not None and _WORKER.running:
            return _WORKER
        _WORKER = ReportRuntime()
        thread = threading.Thread(
            target=_WORKER.run,
            name="SCADA-Report-Runtime",
            daemon=True,
        )
        thread.start()
        print("REPORT RUNTIME WORKER STARTED (EVENT-DRIVEN)")
        return _WORKER
