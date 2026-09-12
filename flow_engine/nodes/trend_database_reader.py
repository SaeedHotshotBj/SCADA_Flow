# =====================================================
# SCADA_FLOW TREND DATABASE READER NODE
# Quantitative behavior preserved; PLC identity resolved per Flow tag.
# =====================================================

from datetime import datetime, timedelta
import json
import jdatetime

from services.trend_query import get_trend_series, get_trend_stats
from database import row_value


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

    @staticmethod
    def _flow_tag_plcs(company_id):
        """Read TagMapper PLC identity from the saved company Flow."""
        try:
            from database import get_company_flow
            flow_json = get_company_flow(company_id)
            if not flow_json:
                return {}
            flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
            nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}
            result = {}
            for node in nodes.values():
                if not isinstance(node, dict) or node.get("name") != "TagMapper":
                    continue
                raw = node.get("data", {}) or {}
                config = raw.get("config", raw) or {}
                mappings = config.get("mappings", [])
                if not isinstance(mappings, list):
                    continue
                for mapping in mappings:
                    if not isinstance(mapping, dict):
                        continue
                    name = str(mapping.get("name", "")).strip()
                    if not name:
                        continue
                    plc_id = TrendDatabaseReader._plc_id(
                        mapping.get("plc_id", mapping.get("PLC_ID"))
                    )
                    if plc_id is not None:
                        result[TrendDatabaseReader._normalize_tag(name)] = plc_id
                # TagMapper is the canonical tag-definition node.
                if result:
                    break
            return result
        except Exception as exc:
            print("TREND TAG PLC MAP ERROR:", repr(exc))
            return {}

    def _resolve_plc_id(self, data, request, tag_name=None, tag_plcs=None):
        explicit = request.get("PLC_ID", request.get("plc_id", data.get("PLC_ID")))
        plc_id = self._plc_id(explicit)
        if plc_id is not None:
            return plc_id

        if tag_name and tag_plcs:
            mapped = tag_plcs.get(self._normalize_tag(tag_name))
            if mapped is not None:
                return mapped

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
        elif start is None or end is None or start >= end:
            data["TrendData"], data["TrendStats"], data["TrendResolution"] = [], {}, {}
            return data

        company_id = data.get("CompanyID", request.get("CompanyID", self.company_id))
        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            data["TrendData"], data["TrendStats"], data["TrendResolution"] = [], {}, {}
            return data

        tag_plcs = self._flow_tag_plcs(company_id)
        explicit_plc = self._plc_id(request.get("PLC_ID", request.get("plc_id", data.get("PLC_ID"))))
        if explicit_plc is None and not tag_plcs:
            single_plc = self._resolve_plc_id(data, request)
            if single_plc is not None:
                tag_plcs = {self._normalize_tag(tag): single_plc for tag in tags}

        trend, stats_by_tag, resolutions, plc_by_tag = [], {}, {}, {}
        for tag in tags:
            try:
                plc_id = self._resolve_plc_id(data, request, tag, tag_plcs)
                if plc_id is None:
                    print("TREND DATABASE READER: PLC_ID unresolved for tag", tag)
                    resolutions[tag] = "error"
                    stats_by_tag[tag] = {"resolution": "error", "min": None, "max": None, "weighted_average": None, "sample_count": 0}
                    continue

                plc_by_tag[tag] = plc_id
                resolution, rows = get_trend_series(company_id, plc_id, tag, start, end)
                resolutions[tag] = resolution
                for row in rows or []:
                    value = self._row_value(row)
                    timestamp = self._row_timestamp(row)
                    if timestamp is None or value is None:
                        continue
                    trend.append({"Tag": tag, "Timestamp": timestamp, "Value": value, "PLC_ID": plc_id})
                stats_by_tag[tag] = get_trend_stats(company_id, plc_id, tag, start, end)
            except Exception as exc:
                resolutions[tag] = "error"
                stats_by_tag[tag] = {"resolution":"error","min":None,"max":None,"weighted_average":None,"sample_count":0}
                print("TREND DATABASE READER ERROR:", "Company=", company_id, "PLC_ID=", plc_by_tag.get(tag), "Tag=", tag, "Error=", repr(exc))

        trend.sort(key=self._sort_timestamp)
        unique_plcs = sorted(set(plc_by_tag.values()))
        data["CompanyID"] = company_id
        data["PLC_ID"] = explicit_plc if explicit_plc is not None else (unique_plcs[0] if len(unique_plcs) == 1 else None)
        data["TrendPLCByTag"] = plc_by_tag
        data["TrendRequest"] = dict(request, Tag=selected_tag if len(tags) == 1 else None, Tags=tags, Start=start, End=end, Calendar=calendar, CompanyID=company_id, PLC_ID=data["PLC_ID"])
        data["TrendData"] = trend
        data["TrendStats"] = stats_by_tag
        data["TrendResolution"] = resolutions
        data["TrendRecordCount"] = len(trend)
        return data
