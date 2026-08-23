"""SCADA_FLOW services package bootstrap."""

# Start the Trend aggregation worker whenever the services package is loaded.
# The database lease inside the worker prevents concurrent processes from
# aggregating the same bucket at the same time.
try:
    from .trend_runtime_fix import start as _start_trend_aggregation

    _start_trend_aggregation()
except Exception as _trend_exc:
    print("TREND AGGREGATION START ERROR:", _trend_exc)
