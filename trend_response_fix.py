"""SCADA_FLOW historical Trend response compatibility layer."""

from flow_engine.nodes.trend_database_reader import TrendDatabaseReader
from flow_engine.nodes.trend_output import TrendOutput


_ORIGINAL_EXECUTE_REQUEST = None


def install(FlowRunner):
    global _ORIGINAL_EXECUTE_REQUEST

    if getattr(FlowRunner, "_trend_response_fix_installed", False):
        return

    _ORIGINAL_EXECUTE_REQUEST = FlowRunner.execute_request

    def _execute_request(self, request):
        trend_request = request.get("TrendRequest") if isinstance(request, dict) else None

        if not isinstance(trend_request, dict):
            return _ORIGINAL_EXECUTE_REQUEST(self, request)

        payload = dict(request)
        payload["CompanyID"] = self.company_id
        trend_request = dict(trend_request)
        trend_request["CompanyID"] = self.company_id
        payload["TrendRequest"] = trend_request

        reader = TrendDatabaseReader({"company_id": self.company_id})
        payload = reader.execute(payload)

        output = TrendOutput({})
        payload = output.execute(payload)

        chart = payload.get("ChartData")
        if isinstance(chart, dict):
            datasets = chart.get("datasets", [])
            stats = chart.get("stats", {})
            print(
                "TREND DIRECT RESPONSE:",
                "Company=", self.company_id,
                "Tag=", trend_request.get("Tag"),
                "Datasets=", len(datasets),
                "Points=", sum(len(ds.get("data", [])) for ds in datasets if isinstance(ds, dict)),
                "Stats=", stats,
            )

        return payload

    FlowRunner.execute_request = _execute_request
    FlowRunner._trend_response_fix_installed = True
