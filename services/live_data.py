"""Process-local live value buffer. Live PLC data is never written to SQLite."""

import math
import threading
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tehran")
LIVE_TREND_RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MAX_AGE_SECONDS = 30
_LOCK = threading.RLock()
_BUFFERS = {}


def _parse_ts(value):
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip().replace("T", " ")
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    pass
        if dt is None:
            return None

    if dt.tzinfo is not None:
        dt = dt.astimezone(TZ).replace(tzinfo=None)
    return dt


def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")


def record_live_items(company_id, items):
    if company_id is None or not isinstance(items, list):
        return 0

    stored = 0
    now = datetime.now(TZ).replace(tzinfo=None)
    with _LOCK:
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                plc_id = int(item["PLC_ID"])
                tag = str(item["TagName"]).strip()
                value = float(item["Value"])
            except (KeyError, TypeError, ValueError):
                continue
            if not tag or not math.isfinite(value):
                continue
            storage_type = str(item.get("StorageType", "LIVE") or "LIVE").strip().upper()
            if storage_type not in {"LIVE", "TIME", "CALCULATED"}:
                continue
            timestamp = _parse_ts(item.get("Timestamp")) or now
            # Storage type is part of the identity. A calculated value with
            # the same TagName must never overwrite a raw LIVE/TIME value.
            key = (int(company_id), plc_id, tag.lower(), storage_type)
            buffer = _BUFFERS.setdefault(key, deque())
            buffer.append((_ts(timestamp), value, tag))
            _cleanup_key(
                buffer,
                now - timedelta(seconds=LIVE_TREND_RETENTION_SECONDS),
            )
            stored += 1
    return stored


def _cleanup_key(buffer, cutoff):
    while buffer and _parse_ts(buffer[0][0]) and _parse_ts(buffer[0][0]) < cutoff:
        buffer.popleft()


def get_live_value(company_id, plc_id, tag_name, max_age_seconds=DEFAULT_MAX_AGE_SECONDS, storage_type=None):
    if company_id is None or plc_id is None or not tag_name:
        return None
    tag_key = str(tag_name).strip().lower()
    requested_storage = str(storage_type or "").strip().upper()
    cutoff = datetime.now(TZ).replace(tzinfo=None) - timedelta(seconds=max(1, int(max_age_seconds)))
    with _LOCK:
        if requested_storage in {"LIVE", "TIME", "CALCULATED"}:
            keys = [(int(company_id), int(plc_id), tag_key, requested_storage)]
        else:
            keys = [(int(company_id), int(plc_id), tag_key, item_storage) for item_storage in ("LIVE", "TIME", "CALCULATED")]
        candidates = []
        retention_cutoff = datetime.now(TZ).replace(tzinfo=None) - timedelta(
            seconds=LIVE_TREND_RETENTION_SECONDS
        )
        for key in keys:
            buffer = _BUFFERS.get(key)
            if not buffer:
                continue
            _cleanup_key(buffer, retention_cutoff)
            if buffer:
                candidates.append(buffer[-1])
        if not candidates:
            return None
        timestamp, value, tag = max(candidates, key=lambda item: _parse_ts(item[0]) or datetime.min)
        return {"PLC_ID": int(plc_id), "TagName": tag, "Value": value, "Timestamp": timestamp}


def get_live_series(company_id, plc_id, tag_name, start=None, end=None, default_minutes=10, storage_type=None):
    if company_id is None or plc_id is None or not tag_name:
        return []

    now = datetime.now(TZ).replace(tzinfo=None)
    start_dt = _parse_ts(start) if start is not None else now - timedelta(minutes=default_minutes)
    end_dt = _parse_ts(end) if end is not None else now
    if start_dt is None or end_dt is None or start_dt > end_dt:
        return []

    tag_key = str(tag_name).strip().lower()
    requested_storage = str(storage_type or "").strip().upper()
    with _LOCK:
        buffers = []
        retention_cutoff = datetime.now(TZ).replace(tzinfo=None) - timedelta(
            seconds=LIVE_TREND_RETENTION_SECONDS
        )
        storages = ((requested_storage,) if requested_storage in {"LIVE", "TIME", "CALCULATED"} else ("LIVE", "TIME", "CALCULATED"))
        for item_storage in storages:
            buffer = _BUFFERS.get((int(company_id), int(plc_id), tag_key, item_storage))
            if buffer:
                _cleanup_key(buffer, retention_cutoff)
                if buffer:
                    buffers.append(buffer)
        if not buffers:
            return []
        result = []
        for buffer in buffers:
            for timestamp, value, tag in buffer:
                dt = _parse_ts(timestamp)
                if dt is None or dt < start_dt or dt > end_dt:
                    continue
                result.append({
                    "Tag": tag,
                    "Timestamp": timestamp,
                    "Value": value,
                    "PLC_ID": int(plc_id),
                })
        result.sort(key=lambda item: _parse_ts(item["Timestamp"]) or datetime.min)
        return result


def get_live_latest_for_tags(company_id, tag_specs, max_age_seconds=DEFAULT_MAX_AGE_SECONDS):
    result = []
    if not isinstance(tag_specs, list):
        return result
    for spec in tag_specs:
        if not isinstance(spec, dict):
            continue
        item = get_live_value(
            company_id,
            spec.get("PLC_ID", spec.get("plc_id")),
            spec.get("tag"),
            max_age_seconds=max_age_seconds,
            storage_type=spec.get("storage", spec.get("StorageType")),
        )
        if item is None:
            continue
        item["title"] = spec.get("title", spec.get("tag"))
        item["unit"] = spec.get("unit", "")
        item["allowed_roles"] = spec.get("allowed_roles", "")
        result.append(item)
    return result


def get_live_register_values(company_id, plc_id, mappings, max_age_seconds=DEFAULT_MAX_AGE_SECONDS):
    registers = {}
    if not isinstance(mappings, list):
        return registers
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        item = get_live_value(
            company_id,
            plc_id,
            mapping.get("name"),
            max_age_seconds=max_age_seconds,
            storage_type=mapping.get("storage", mapping.get("StorageType")),
        )
        if item is None:
            continue
        register = mapping.get("register")
        if register in (None, ""):
            continue
        registers[str(register)] = item["Value"]
    return registers


def clear():
    with _LOCK:
        _BUFFERS.clear()


__all__ = [
    "record_live_items",
    "get_live_value",
    "get_live_series",
    "get_live_latest_for_tags",
    "get_live_register_values",
    "clear",
]
