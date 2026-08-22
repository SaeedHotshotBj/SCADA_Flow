"""Local SCADA_FLOW console filter.

During local timeout testing, suppress normal runtime prints and keep only
messages related to timeout handling so the console stays useful.
"""

import builtins

_original_print = builtins.print


def _quiet_print(*args, **kwargs):
    message = " ".join(str(arg) for arg in args)

    if "TIMEOUT" in message.upper():
        _original_print(*args, **kwargs)


builtins.print = _quiet_print
