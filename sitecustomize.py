"""SCADA_FLOW local runtime bootstrap."""

import builtins
import os
import sys

_original_print = builtins.print


def _quiet_print(*args, **kwargs):
    message = " ".join(str(arg) for arg in args)
    if "TIMEOUT" in message.upper():
        _original_print(*args, **kwargs)


builtins.print = _quiet_print


def _is_scada_server_process():
    executable = os.path.basename(sys.argv[0] or "").lower()
    args = [os.path.basename(str(item)).lower() for item in sys.argv[1:]]

    if executable == "app.py":
        return True

    if "app.py" in args:
        return True

    if "gunicorn" in executable:
        return True

    if executable in {"flask", "flask.exe"} and "run" in args:
        return True

    return False


if _is_scada_server_process():
    try:
        from services.trend_runtime_fix import start
        start()
    except Exception as exc:
        _original_print("TREND AGGREGATION START ERROR:", exc)

    try:
        from flow_runner import FlowRunner
        from trend_response_fix import install as install_trend_response_fix
        install_trend_response_fix(FlowRunner)
    except Exception as exc:
        _original_print("TREND RESPONSE FIX ERROR:", exc)
