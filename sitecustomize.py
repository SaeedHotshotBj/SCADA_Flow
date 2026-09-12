"""Compatibility-only sitecustomize.

SCADA_FLOW no longer performs runtime initialization from Python's implicit
``sitecustomize`` hook. Worker startup, Flask hooks, PLC-flow synchronization,
and Drawflow normalization are owned by the explicit application bootstrap.

Keeping this module as a documented no-op avoids surprising interpreter-level
side effects while remaining compatible with environments that automatically
import ``sitecustomize``.
"""

# Intentionally empty: application startup belongs to app.py / services.runtime_bootstrap.
