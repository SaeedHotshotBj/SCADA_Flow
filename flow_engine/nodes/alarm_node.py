# =====================================================
# SCADA_FLOW ALARM NODE
# PLC-aware alarm history and per-rule PLC ownership.
# =====================================================

from database import get_connection
from datetime import datetime


def insert_alarm_safe(company_id, plc_id, tag, value, message):
    conn = get_connection()
    try:
        conn.execute("INSERT INTO AlarmHistory(CompanyID,PLC_ID,AlarmText,AlarmValue,Timestamp) VALUES(?,?,?,?,?)", (company_id, plc_id, message, value, datetime.now()))
        conn.commit()
    finally:
        conn.close()


class AlarmNode:
    def __init__(self, config=None):
        self.config = config or {}
        self.alarms = self.config.get("alarms", [])
        self.memory = {}

    @staticmethod
    def _plc_id(value):
        try: return int(value)
        except (TypeError,ValueError): return None

    def execute(self, data=None):
        data=data or {}
        tags=data.get("Tags",{}) or {}
        company_id=self.config.get("company_id",1)
        runtime_plc=self._plc_id(data.get("PLC_ID", (data.get("PLC") or {}).get("PLC_ID")))
        if runtime_plc is None: return data

        for alarm in self.alarms:
            if not isinstance(alarm,dict): continue
            configured_plc=self._plc_id(alarm.get("plc_id",alarm.get("PLC_ID",runtime_plc)))
            if configured_plc is not None and configured_plc != runtime_plc: continue
            tag=str(alarm.get("tag","")).strip()
            condition=alarm.get("condition")
            limit=alarm.get("limit")
            message=alarm.get("message","Alarm")
            if tag not in tags: continue
            value=tags[tag]
            try:
                active = value > float(limit) if condition==">" else value < float(limit) if condition=="<" else value == float(limit) if condition=="==" else None
            except (TypeError,ValueError): active=None
            if active is None: continue
            key=(int(company_id),runtime_plc,tag.lower())
            previous=self.memory.get(key,False)
            self.memory[key]=active
            if active and not previous:
                try:
                    insert_alarm_safe(company_id,runtime_plc,tag,value,message)
                    print("ALARM:",message,"PLC_ID:",runtime_plc)
                except Exception as exc: print("ALARM DATABASE ERROR:",exc)
        return data
