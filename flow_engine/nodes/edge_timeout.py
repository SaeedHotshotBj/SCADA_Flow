# =====================================================
# SCADA FLOW
# EDGE TIMEOUT NODE
# =====================================================


class EdgeTimeout:
    """Declarative Flow node for configuring Edge communication timeout.

    The actual timeout monitoring is performed by the server-side
    edge-timeout worker. This node only carries the timeout setting inside
    the company's saved Flow.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")

        try:
            self.timeout_seconds = float(
                self.config.get("timeout_seconds", 10)
            )
        except (TypeError, ValueError):
            self.timeout_seconds = 10.0

        if self.timeout_seconds <= 0:
            self.timeout_seconds = 10.0

    def execute(self, data=None):
        if data is None:
            data = {}

        data["EdgeTimeout"] = {
            "company_id": self.company_id,
            "timeout_seconds": self.timeout_seconds,
        }
        return data
