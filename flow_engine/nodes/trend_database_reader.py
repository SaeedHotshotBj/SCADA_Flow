# =====================================================
# SCADA_FLOW TREND DATABASE READER NODE
# Quantitative behavior preserved; PLC identity added.
# =====================================================

from datetime import datetime, timedelta
import jdatetime

from services.trend_aggregation import get_trend_series, get_trend_stats, start_aggregation_worker
from database import row_value, get_company_flow

try:
    start_aggregation_worker()
except Exception as exc:
    print("TREND AGGREGATION START ERROR:", exc)


class TrendDatabaseReader:
    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")

    @staticmethod
    def _normalize_digits(text):
        if text is None:
            return text
        return str(text).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))

    @staticmethod
    def _normalize_tag(value):
        return str(value or "").strip().lower()

    @classmethod
    def _split_tags(cls, value):
        if value is None:
            return []
        values = value if isinstance(value, (list, tuple, set)) else str(value).replace(";", ",").split(",")
        result, seen = [], set()
        for item in values:
            tag = str(item).strip()
            key = cls._normalize_tag(tag)
            if tag and key not in seen:
                seen.add(key)
                result.append(tag)
        return result

    @staticmethod
    def _plc_id(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _flow_tag_plc_map(self, company_id):
        """Resolve each TagMapper tag to the PLC_ID declared by that company flow."""
        result = {}
        try:
            flow_json = get_company_flow(company_id)
            if not flow_json:
                return result
            import json
            flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
            nodes = (
                flow.get("drawflow", {})
                    .get("Home", {})
                    .get("data", {})
            )
            if not isinstance(nodes, dict):
                return result

            for node in nodes.values():
                if not isinstance(node, dict) or node.get("name") != "TagMapper":
                    continue
                mappings = (node.get("data", {}) or {}).get("mappings", [])
                if not isinstance(mappings, list):
                    continue
                for item in mappings:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "")).strip()
                    if not name:
                        continue
                    plc_id = self._plc_id(item.get("plc_id", item.get("PLC_ID")))
                    if plc_id is not None:
                        result[self._normalize_tag(name)] = plc_id
                break
        except Exception as exc:
            print("TREND TAG PLC MAP ERROR:", "Company=", company_id, "Error=", repr(exc))
        return result

    def _resolve_plc_id(self, data, request):
        value = request.get("PLC_ID", request.get("plc_id", data.get("PLC_ID")))
        plc_id = self._plc_id(value)
        if plc_id is not None:
            return plc_id
        company_id = data.get("CompanyID", request.get("CompanyID", self.company_id))
        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            return None
        from database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute("SELECT PLC_ID FROM PLCs WHERE CompanyID=? ORDER BY PLC_ID", (company_id,)).fetchall()
            return int(rows[0]["PLC_ID"]) if len(rows) == 1 else None
        finally:
            conn.close()

    def normalize_date(self, value, calendar, timezone_offset=None):
        if not value:
            return None
        text = self._normalize_digits(value).strip().replace("T", " ")
        if calendar == "Jalali":
            text = text.replace("-", "/")
            for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
                try:
                    return jdatetime.datetime.strptime(text, fmt).togregorian()
                except ValueError:
                    pass
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
        return None

    @staticmethod
    def _row_timestamp(row):
        return row_value(row, "Timestamp", 0)

    @staticmethod
    def _row_value(row):
        return row_value(row, "Value", 1)

    @staticmethod
    def _sort_timestamp(item):
        value = item.get("Timestamp")
        if value is None:
            return datetime.min
        if isinstance(value, datetime):
            return value
        text = str(value).strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
        return datetime.min

    def execute(self, data=None):
        data = data or {}
        request = data.get("TrendRequest", {}) or {}
        selected_tag = request.get("Tag")
        tags = self._split_tags(request.get("Tags")) or self._split_tags(selected_tag)
        calendar = request.get("Calendar", "Gregorian")
        start = self.normalize_date(request.get("Start"), calendar)
        end = self.normalize_date(request.get("End"), calendar)

        if start is None and end is None:
            end = datetime.now().replace(microsecond=0)
            start = end - timedelta(hours=2)
        elif start is None or end is None:
            data["TrendData"], data["TrendStats"], data["TrendResolution"] = [], {}, {}
            return data

        company_id = data.get("CompanyID", request.get("CompanyID", self.company_id))
        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            data["TrendData"], data["TrendStats"], data["TrendResolution"] = [], {}, {}
            return data

        explicit_plc_id = self._plc_id(
            request.get("PLC_ID", request.get("plc_id", data.get("PLC_ID")))
        )
        default_plc_id = explicit_plc_id if explicit_plc_id is not None else self._resolve_plc_id(data, request)
        flow_tag_plcs = self._flow_tag_plc_map(company_id)

        trend, stats_by_tag, resolutions = [], {}, {}
        resolved_plcs = {}
        for tag in tags:
            tag_key = self._normalize_tag(tag)
            tag_plc_id = explicit_plc_id
            if tag_plc_id is None:
                tag_plc_id = flow_tag_plcs.get(tag_key, default_plc_id)

            if tag_plc_id is None:
                print("TREND DATABASE READER: PLC_ID could not be resolved", "Company=", company_id, "Tag=", tag)
                resolutions[tag] = "error"
                stats_by_tag[tag] = {"resolution": "error", "min": None, "max": None, "weighted_average": None, "sample_count": 0}
                continue

            resolved_plcs[tag] = int(tag_plc_id)
            try:
                resolution, rows = get_trend_series(company_id, int(tag_plc_id), tag, start, end)
                resolutions[tag] = resolution
                for row in rows or []:
                    value = self._row_value(row)
                    timestamp = self._row_timestamp(row)
                    if timestamp is None or value is None:
                        continue
                    trend.append({"Tag": tag, "Timestamp": timestamp, "Value": value, "PLC_ID": int(tag_plc_id)})
                stats_by_tag[tag] = get_trend_stats(company_id, int(tag_plc_id), tag, start, end)
            except Exception as exc:
                resolutions[tag] = "error"
                stats_by_tag[tag] = {"resolution":"error","min":None,"max":None,"weighted_average":None,"sample_count":0}
                print("TREND DATABASE READER ERROR:", "Company=", company_id, "PLC_ID=", tag_plc_id, "Tag=", tag, "Error=", repr(exc))

        trend.sort(key=self._sort_timestamp)
        data["CompanyID"] = company_id
        if explicit_plc_id is not None:
            data["PLC_ID"] = explicit_plc_id
        elif len(set(resolved_plcs.values())) == 1:
            data["PLC_ID"] = next(iter(resolved_plcs.values()))
        else:
            data.pop("PLC_ID", None)
        data["TrendRequest"] = dict(request, Tag=selected_tag if len(tags) == 1 else None, Tags=tags, Start=start, End=end, Calendar=calendar, CompanyID=company_id)
        data["TrendData"] = trend
        data["TrendStats"] = stats_by_tag
        data["TrendResolution"] = resolutions
        data["TrendRecordCount"] = len(trend)
        data["TrendTagPLCs"] = resolved_plcs
        return data