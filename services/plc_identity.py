# SCADA_FLOW PLC identity / multi-PLC schema helpers
import datetime
import json


def _table_columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def ensure_plc_identity_schema():
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute("BEGIN")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in ("Tags", "PLC_Data", "AlarmHistory", "ReportHistory"):
            if table not in tables:
                continue
            if "PLC_ID" not in _table_columns(conn, table):
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN PLC_ID INTEGER')
            conn.execute(f'''UPDATE "{table}" SET PLC_ID=(SELECT MIN(p.PLC_ID) FROM PLCs p WHERE p.CompanyID="{table}".CompanyID)
                             WHERE PLC_ID IS NULL AND 1=(SELECT COUNT(*) FROM PLCs p2 WHERE p2.CompanyID="{table}".CompanyID)''')

        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "Flows" in tables and "PLCs" in tables:
            for row in conn.execute("SELECT FlowID,CompanyID,FlowJson FROM Flows WHERE CompanyID IS NOT NULL").fetchall():
                plc_rows=conn.execute("SELECT PLC_ID FROM PLCs WHERE CompanyID=? ORDER BY PLC_ID",(row["CompanyID"],)).fetchall()
                if not plc_rows: continue
                default_plc=int(plc_rows[0]["PLC_ID"])
                try: flow=json.loads(row["FlowJson"] or "{}")
                except Exception: continue
                changed=False
                nodes=flow.get("drawflow",{}).get("Home",{}).get("data",{}) or {}
                for node in nodes.values():
                    if not isinstance(node,dict): continue
                    config=node.get("data",{}) or {}
                    config=config.get("config",config) or {}
                    if node.get("name")=="PLCReader" and config.get("plc_id") in (None,""):
                        config["plc_id"]=default_plc; changed=True
                    if node.get("name")=="TagMapper":
                        for mapping in config.get("mappings",[]) if isinstance(config.get("mappings",[]),list) else []:
                            if isinstance(mapping,dict) and mapping.get("plc_id",mapping.get("PLC_ID")) in (None,""):
                                mapping["plc_id"]=default_plc; changed=True
                if changed:
                    conn.execute("UPDATE Flows SET FlowJson=?,LastModified=datetime('now','localtime') WHERE FlowID=?",(json.dumps(flow,ensure_ascii=False),row["FlowID"]))

        if "FlowTriggerState" in tables:
            cols=_table_columns(conn,"FlowTriggerState")
            if "PLC_ID" not in cols:
                conn.execute("ALTER TABLE FlowTriggerState ADD COLUMN PLC_ID INTEGER")
            conn.execute("""UPDATE FlowTriggerState SET PLC_ID=(SELECT MIN(p.PLC_ID) FROM PLCs p WHERE p.CompanyID=FlowTriggerState.CompanyID)
                           WHERE PLC_ID IS NULL AND 1=(SELECT COUNT(*) FROM PLCs p2 WHERE p2.CompanyID=FlowTriggerState.CompanyID)""")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_flow_trigger_state_company_plc_register ON FlowTriggerState(CompanyID,PLC_ID,TriggerRegister)")

        for table,idx,cols in (
            ("Tags","idx_tags_company_plc_name","CompanyID,PLC_ID,TagName"),
            ("PLC_Data","idx_plc_data_company_plc_tag_time","CompanyID,PLC_ID,TagName,Timestamp"),
            ("TagHistory","idx_tag_history_company_plc_tag_time","CompanyID,PLC_ID,TagName,Timestamp"),
            ("AlarmHistory","idx_alarm_history_company_plc_time","CompanyID,PLC_ID,Timestamp"),
            ("ReportHistory","idx_report_history_company_plc_time","CompanyID,PLC_ID,Timestamp"),
        ):
            if table in tables:
                conn.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON {table}({cols})")

        if "TagHistory" in tables and "PLC_Data" in tables:
            conn.execute('''CREATE TRIGGER IF NOT EXISTS trg_tag_history_stamp_plc_data AFTER INSERT ON TagHistory BEGIN
                UPDATE PLC_Data SET PLC_ID=NEW.PLC_ID WHERE ID=(SELECT ID FROM PLC_Data WHERE CompanyID=NEW.CompanyID AND LOWER(TagName)=LOWER(NEW.TagName) AND Timestamp=NEW.Timestamp AND PLC_ID IS NULL ORDER BY ID DESC LIMIT 1);
            END''')
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def insert_plc_data(company_id,plc_id,tag,value,storage_type,timestamp=None):
    if timestamp is None: timestamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    from database import get_connection
    conn=get_connection()
    try:
        conn.execute("INSERT INTO PLC_Data(CompanyID,PLC_ID,TagName,Value,StorageType,Timestamp) VALUES(?,?,?,?,?,?)",(int(company_id),int(plc_id),str(tag),float(value),storage_type,timestamp)); conn.commit()
    finally: conn.close()


def get_latest_tag_values(company_id,plc_id,tag_names):
    if not tag_names:return {}
    from database import get_connection
    conn=get_connection()
    try:
        result={}
        for tag in tag_names:
            row=conn.execute("SELECT Value,Timestamp FROM PLC_Data WHERE CompanyID=? AND PLC_ID=? AND LOWER(TagName)=LOWER(?) ORDER BY Timestamp DESC,ID DESC LIMIT 1",(int(company_id),int(plc_id),tag)).fetchone()
            if row: result[tag]={"value":row["Value"],"timestamp":row["Timestamp"]}
        return result
    finally: conn.close()


def get_trend_data(company_id,plc_id,tag_name,start=None,end=None):
    from database import get_connection,_format_timestamp
    conn=get_connection()
    try:
        start=_format_timestamp(start); end=_format_timestamp(end)
        # Prefer the PLC-aware aggregation tables. Fall back to raw history.
        if start is not None and end is not None:
            seconds=(end-start).total_seconds() if hasattr(end,"total_seconds") else 0
            table="TrendMinute" if seconds<=7200 else ("TrendHour" if seconds<=172800 else "TrendDay")
            try:
                rows=conn.execute(f"SELECT PeriodStart AS Timestamp,WeightedAverage AS Value FROM {table} WHERE CompanyID=? AND PLC_ID=? AND LOWER(TagName)=LOWER(?) AND PeriodStart<? AND PeriodEnd>? ORDER BY PeriodStart",(int(company_id),int(plc_id),tag_name,end,start)).fetchall()
                if rows:return rows
            except Exception: pass
        sql="SELECT Timestamp,Value FROM PLC_Data WHERE CompanyID=? AND PLC_ID=? AND LOWER(TagName)=LOWER(?)"
        params=[int(company_id),int(plc_id),tag_name]
        if start is not None and end is not None: sql+=" AND Timestamp BETWEEN ? AND ?"; params.extend([start,end])
        sql+=" ORDER BY Timestamp ASC"
        return conn.execute(sql,params).fetchall()
    finally: conn.close()
