"""SCADA_FLOW services package.

Importing ``services`` never starts a thread, registers a Flask hook, patches a
class, or touches the database. Process-level startup is explicit in
``services.runtime_bootstrap.bootstrap``.
"""

__all__ = []
