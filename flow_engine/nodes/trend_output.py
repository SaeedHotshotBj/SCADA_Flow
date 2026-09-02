# =====================================================
# SCADA_FLOW TREND OUTPUT NODE
# =====================================================

from datetime import datetime, timezone, timedelta
import jdatetime

from services.trend_aggregation import get_trend_series, get_trend_stats


class TrendOutput:

    def __init__(self, config=None):
        self.config = config or {}

    def _parse_timestamp(self, value):
        if value is None:
            return None
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            dt = value
            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.replace(tzinfo=None)
            return dt

        text = str(value).strip().replace("T", " ")
        if text.endswith("Z"):
            text = text[:-1]

        text = text.translate(str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789"
        ))

        if "/" in text:
            for fmt in (
                "%Y/%m/%d %H:%M:%S.%f",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
            ):
                try:
                    return jdatetime.datetime.strptime(text, fmt).togregorian()
                except Exception:
                    pass

        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                pass

        try:
            return datetime.fromisoformat(text).replace(tzinfo=None)
        except Exception:
            return None

    def convert_time(self, value):
        dt = self._parse_timestamp(value)
        if dt is None:
            return None
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)

    def jalali_label(self, value):
        try:
            dt = self._parse_timestamp(value)
            if dt is None:
                return str(value)
            jdt = jdatetime.datetime.fromgregorian(datetime=dt)
            return jdt.strftime("%Y/%m/%d %H:%M:%S")
        except Exception:
            return str(value)

    def _point(self, timestamp, value):
        x = self.convert_time(timestamp)
        if x is None:
            return None
        try:
            y = float(value)
        except (TypeError, ValueError):
            return None
        return {
            "x": x,
            "y": y,
            "label": self.jalali_label(timestamp),
        }

    @staticmethod
    def _normalize_tag(value):
        return str(value or "").strip().lower()

    @staticmethod
    def _stats_for_tag(stats, tag):
        if not isinstance(stats, dict):
            return {}
        direct = stats.get(tag)
        if isinstance(direct, dict):
            return direct
        wanted = str(tag or "").strip().lower()
        for key, value in stats.items():
            if str(key).strip().lower() == wanted and isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _normalize_plc_id(value):
        try:
            value = int(value)
            return value if value > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _debug_log(company_id, plc_id, level, message):
        """Write Trend diagnostics to the existing Master Logs channel."""
        conn = None
        try:
            from database import get_connection
            conn = get_connection()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS EdgeTimeoutDiagnosticLog (
                    LogID INTEGER PRIMARY KEY AUTOINCREMENT,
                    CompanyID INTEGER NOT NULL DEFAULT 0,
                    PLC_ID INTEGER,
                    Level TEXT NOT NULL DEFAULT 'INFO',
                    Message TEXT NOT NULL DEFAULT '',
                    Timestamp TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute("""
                INSERT INTO EdgeTimeoutDiagnosticLog
                (CompanyID, PLC_ID, Level, Message, Timestamp)
                VALUES (?, ?, ?, ?, datetime('now','localtime'))
            """, (
                int(company_id or 0),
                plc_id,
                str(level).upper(),
                str(message),
            ))
            conn.commit()
        except Exception:
            try:
                if conn is not None:
                    conn.rollback()
            except Exception:
                pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _load_card_trend_directly(self, data):
        """Guarantee a MachineCard trend uses CompanyID + PLC_ID + TagName."""
        request = data.get("TrendRequest", {}) or {}
        selected_tag = str(request.get("Tag") or "").strip()
        if not selected_tag:
            return

        company_id = data.get("CompanyID", request.get("CompanyID", self.config.get("company_id")))
        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            self._debug_log(0, None, "ERROR", f"TREND_DIRECT_INVALID_COMPANY tag={selected_tag}")
            return

        plc_id = self._normalize_plc_id(
            request.get("PLC_ID", request.get("plc_id", data.get("PLC_ID")))
        )
        if plc_id is None:
            self._debug_log(
                company_id,
                None,
                "WARNING",
                f"TREND_DIRECT_NO_PLC company={company_id} tag={selected_tag}",
            )
            return

        calendar = request.get("Calendar", "Gregorian")
        start = self._parse_timestamp(request.get("Start"))
        end = self._parse_timestamp(request.get("End"))

        if start is None and end is None:
            end = datetime.now().replace(microsecond=0)
            start = end - timedelta(hours=2)
        elif start is None or end is None:
            self._debug_log(
                company_id,
                plc_id,
                "WARNING",
                f"TREND_DIRECT_INVALID_RANGE company={company_id} plc={plc_id} tag={selected_tag} start={request.get('Start')} end={request.get('End')}",
            )
            return

        if start > end:
            start, end = end, start

        self._debug_log(
            company_id,
            plc_id,
            "INFO",
            f"TREND_DIRECT_START company={company_id} plc={plc_id} tag={selected_tag} calendar={calendar} start={start} end={end}",
        )

        try:
            resolution, rows = get_trend_series(
                company_id,
                plc_id,
                selected_tag,
                start,
                end,
            )

            trend = []
            for row in rows or []:
                timestamp = row["Timestamp"] if "Timestamp" in row.keys() else row[0]
                value = row["Value"] if "Value" in row.keys() else row[1]
                if timestamp is None or value is None:
                    continue
                trend.append({
                    "Tag": selected_tag,
                    "Timestamp": timestamp,
                    "Value": value,
                    "PLC_ID": plc_id,
                })

            stats = get_trend_stats(
                company_id,
                plc_id,
                selected_tag,
                start,
                end,
            )

            data["CompanyID"] = company_id
            data["PLC_ID"] = plc_id
            data["TrendData"] = trend
            data["TrendStats"] = {selected_tag: stats}
            data["TrendResolution"] = {selected_tag: resolution}
            data["TrendRecordCount"] = len(trend)
            data["TrendRequest"] = dict(
                request,
                Tag=selected_tag,
                Tags=[selected_tag],
                Start=start,
                End=end,
                Calendar=calendar,
                CompanyID=company_id,
                PLC_ID=plc_id,
            )

            self._debug_log(
                company_id,
                plc_id,
                "INFO",
                f"TREND_DIRECT_RESULT company={company_id} plc={plc_id} tag={selected_tag} source={resolution} rows={len(trend)}",
            )
        except Exception as exc:
            self._debug_log(
                company_id,
                plc_id,
                "ERROR",
                f"TREND_DIRECT_ERROR company={company_id} plc={plc_id} tag={selected_tag} error={exc!r}",
            )

    def execute(self, data=None):
        if data is None:
            data = {}
        if "ReportRequest" in data:
            return data

        request = data.get("TrendRequest", {}) or {}
        selected_tag = request.get("Tag")

        # MachineCard trend requests carry an explicit PLC_ID. Resolve them
        # before producing ChartData so an older/legacy trend branch cannot
        # drop the PLC identity and accidentally return an empty/mixed series.
        if selected_tag and self._normalize_plc_id(request.get("PLC_ID", request.get("plc_id", data.get("PLC_ID")))) is not None:
            self._load_card_trend_directly(data)

        trend_data = data.get("TrendData", []) or []
        selected_key = self._normalize_tag(selected_tag)

        grouped = {}
        for item in trend_data:
            tag = item.get("Tag")
            if not tag:
                continue
            point = self._point(item.get("Timestamp"), item.get("Value"))
            if point is None:
                continue
            key = self._normalize_tag(tag)
            grouped.setdefault(key, {
                "tag": tag,
                "title": tag,
                "data": [],
                "stepped": "after",
            })["data"].append(point)

        for group in grouped.values():
            group["data"].sort(key=lambda p: p["x"])

        if selected_key:
            output = [grouped[selected_key]] if selected_key in grouped else []
        else:
            requested_tags = request.get("Tags", []) or []
            output = []
            seen = set()
            for tag in requested_tags:
                key = self._normalize_tag(tag)
                if key in grouped and key not in seen:
                    output.append(grouped[key])
                    seen.add(key)
            for key, group in grouped.items():
                if key not in seen:
                    output.append(group)

        stats = data.get("TrendStats", {}) or {}
        resolutions = data.get("TrendResolution", {}) or {}

        selected_stats = self._stats_for_tag(stats, selected_tag) if selected_tag else {}
        result_stats = selected_stats if selected_tag else stats
        resolution = selected_stats.get("resolution") if selected_stats else None
        if not resolution and selected_tag:
            resolution = resolutions.get(selected_tag)

        data["ChartData"] = {
            "datasets": output,
            "stats": result_stats,
            "resolutions": resolutions,
            "multi": len(output) > 1,
            "selected": selected_tag,
        }

        return data
