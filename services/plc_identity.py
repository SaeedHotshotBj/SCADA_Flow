# SCADA_FLOW PLC identity / multi-PLC schema helpers
import datetime
import json


def _table_columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _extract_flow_nodes(flow_json):
    try:
        flow = json.loads(flow_json or "{}") if isinstance(flow_json, str) else (flow_json or {})
    except Exception:
        return []

    nodes = (
        flow.get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )
    return list(nodes.values()) if isinstance(nodes, dict) else []


def _extract_plc_readers(flow_json):
    readers = []

    for node in _extract_flow_nodes(flow_json):
        if not isinstance(node, dict) or node.get("name") != "PLCReader":
            continue

        data = node.get("data", {}) or {}
        config = data.get("config", data) or {}
        if not isinstance(config, dict):
            continue

        raw_plc_id = config.get("plc_id", config.get("PLC_ID"))
        try:
            plc_id = int(raw_plc_id)
        except (TypeError, ValueError):
            continue

        if plc_id <= 0:
            continue

        try:
            port = int(config.get("port", 502))
        except (TypeError, ValueError):
            port = 502

        try:
            slave = int(config.get("slave", 1))
        except (TypeError, ValueError):
            slave = 1

        ip = str(config.get("ip", "")).strip()
        if not ip:
            continue

        name = str(
            config.get("plc_name")
            or config.get("name")
            or "PLC"
        ).strip() or "PLC"

        readers.append({
            "PLC_ID": plc_id,
            "PLC_Name": name,
            "PLC_IP": ip,
            "PLC_Port": port,
            "Slave_ID": slave,
        })

    return readers


def _sync_flow_plcs(conn, company_id, flow_json):
    """Create/update PLC records declared by PLCReader nodes.

    PLC_ID is globally unique because PLCs.PLC_ID is the primary key.
    A PLC_ID already owned by another company is never moved.
    """
    company_id = int(company_id)

    for plc in _extract_plc_readers(flow_json):
        existing = conn.execute(
            "SELECT CompanyID FROM PLCs WHERE PLC_ID = ? LIMIT 1",
            (plc["PLC_ID"],),
        ).fetchone()

        if existing is not None and int(existing["CompanyID"]) != company_id:
            print(
                "PLC IDENTITY CONFLICT:",
                "PLC_ID=", plc["PLC_ID"],
                "ExistingCompany=", existing["CompanyID"],
                "FlowCompany=", company_id,
            )
            continue

        if existing is None:
            conn.execute(
                """
                INSERT INTO PLCs
                (
                    PLC_ID,
                    CompanyID,
                    PLC_Name,
                    PLC_IP,
                    PLC_Port,
                    Slave_ID
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    plc["PLC_ID"],
                    company_id,
                    plc["PLC_Name"],
                    plc["PLC_IP"],
                    plc["PLC_Port"],
                    plc["Slave_ID"],
                ),
            )
        else:
            conn.execute(
                """
                UPDATE PLCs
                SET
                    CompanyID = ?,
                    PLC_Name = ?,
                    PLC_IP = ?,
                    PLC_Port = ?,
                    Slave_ID = ?
                WHERE PLC_ID = ?
                """,
                (
                    company_id,
                    plc["PLC_Name"],
                    plc["PLC_IP"],
                    plc["PLC_Port"],
                    plc["Slave_ID"],
                    plc["PLC_ID"],
                ),
            )


def _create_flow_plc_triggers(conn):
    """Keep PLCs synchronized whenever a company flow is inserted/updated."""
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS trg_flows_validate_plc_identity_insert
        BEFORE INSERT ON Flows
        BEGIN
            SELECT RAISE(ABORT, 'PLC_ID belongs to another company')
            WHERE EXISTS (
                SELECT 1
                FROM json_each(NEW.FlowJson, '$.drawflow.Home.data') AS n
                JOIN PLCs p
                  ON p.PLC_ID = CAST(
                        COALESCE(
                            json_extract(n.value, '$.data.config.plc_id'),
                            json_extract(n.value, '$.data.plc_id')
                        ) AS INTEGER
                     )
                WHERE json_extract(n.value, '$.name') = 'PLCReader'
                  AND COALESCE(
                        json_extract(n.value, '$.data.config.plc_id'),
                        json_extract(n.value, '$.data.plc_id')
                      ) IS NOT NULL
                  AND p.CompanyID <> NEW.CompanyID
            );
        END;

        CREATE TRIGGER IF NOT EXISTS trg_flows_validate_plc_identity_update
        BEFORE UPDATE OF CompanyID, FlowJson ON Flows
        BEGIN
            SELECT RAISE(ABORT, 'PLC_ID belongs to another company')
            WHERE EXISTS (
                SELECT 1
                FROM json_each(NEW.FlowJson, '$.drawflow.Home.data') AS n
                JOIN PLCs p
                  ON p.PLC_ID = CAST(
                        COALESCE(
                            json_extract(n.value, '$.data.config.plc_id'),
                            json_extract(n.value, '$.data.plc_id')
                        ) AS INTEGER
                     )
                WHERE json_extract(n.value, '$.name') = 'PLCReader'
                  AND COALESCE(
                        json_extract(n.value, '$.data.config.plc_id'),
                        json_extract(n.value, '$.data.plc_id')
                      ) IS NOT NULL
                  AND p.CompanyID <> NEW.CompanyID
            );
        END;

        CREATE TRIGGER IF NOT EXISTS trg_flows_sync_plcs_insert
        AFTER INSERT ON Flows
        BEGIN
            INSERT INTO PLCs
            (
                PLC_ID,
                CompanyID,
                PLC_Name,
                PLC_IP,
                PLC_Port,
                Slave_ID
            )
            SELECT
                CAST(
                    COALESCE(
                        json_extract(n.value, '$.data.config.plc_id'),
                        json_extract(n.value, '$.data.plc_id')
                    ) AS INTEGER
                ),
                NEW.CompanyID,
                COALESCE(
                    json_extract(n.value, '$.data.config.plc_name'),
                    json_extract(n.value, '$.data.config.name'),
                    json_extract(n.value, '$.data.plc_name'),
                    'PLC'
                ),
                TRIM(COALESCE(
                    json_extract(n.value, '$.data.config.ip'),
                    json_extract(n.value, '$.data.ip'),
                    ''
                )),
                CAST(COALESCE(
                    json_extract(n.value, '$.data.config.port'),
                    json_extract(n.value, '$.data.port'),
                    502
                ) AS INTEGER),
                CAST(COALESCE(
                    json_extract(n.value, '$.data.config.slave'),
                    json_extract(n.value, '$.data.slave'),
                    1
                ) AS INTEGER)
            FROM json_each(NEW.FlowJson, '$.drawflow.Home.data') AS n
            WHERE json_extract(n.value, '$.name') = 'PLCReader'
              AND CAST(COALESCE(
                    json_extract(n.value, '$.data.config.plc_id'),
                    json_extract(n.value, '$.data.plc_id')
                  ) AS INTEGER) > 0
              AND TRIM(COALESCE(
                    json_extract(n.value, '$.data.config.ip'),
                    json_extract(n.value, '$.data.ip'),
                    ''
                  )) <> ''
            ON CONFLICT(PLC_ID) DO UPDATE SET
                CompanyID = excluded.CompanyID,
                PLC_Name = excluded.PLC_Name,
                PLC_IP = excluded.PLC_IP,
                PLC_Port = excluded.PLC_Port,
                Slave_ID = excluded.Slave_ID;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_flows_sync_plcs_update
        AFTER UPDATE OF CompanyID, FlowJson ON Flows
        BEGIN
            INSERT INTO PLCs
            (
                PLC_ID,
                CompanyID,
                PLC_Name,
                PLC_IP,
                PLC_Port,
                Slave_ID
            )
            SELECT
                CAST(
                    COALESCE(
                        json_extract(n.value, '$.data.config.plc_id'),
                        json_extract(n.value, '$.data.plc_id')
                    ) AS INTEGER
                ),
                NEW.CompanyID,
                COALESCE(
                    json_extract(n.value, '$.data.config.plc_name'),
                    json_extract(n.value, '$.data.config.name'),
                    json_extract(n.value, '$.data.plc_name'),
                    'PLC'
                ),
                TRIM(COALESCE(
                    json_extract(n.value, '$.data.config.ip'),
                    json_extract(n.value, '$.data.ip'),
                    ''
                )),
                CAST(COALESCE(
                    json_extract(n.value, '$.data.config.port'),
                    json_extract(n.value, '$.data.port'),
                    502
                ) AS INTEGER),
                CAST(COALESCE(
                    json_extract(n.value, '$.data.config.slave'),
                    json_extract(n.value, '$.data.slave'),
                    1
                ) AS INTEGER)
            FROM json_each(NEW.FlowJson, '$.drawflow.Home.data') AS n
            WHERE json_extract(n.value, '$.name') = 'PLCReader'
              AND CAST(COALESCE(
                    json_extract(n.value, '$.data.config.plc_id'),
                    json_extract(n.value, '$.data.plc_id')
                  ) AS INTEGER) > 0
              AND TRIM(COALESCE(
                    json_extract(n.value, '$.data.config.ip'),
                    json_extract(n.value, '$.data.ip'),
                    ''
                  )) <> ''
            ON CONFLICT(PLC_ID) DO UPDATE SET
                CompanyID = excluded.CompanyID,
                PLC_Name = excluded.PLC_Name,
                PLC_IP = excluded.PLC_IP,
                PLC_Port = excluded.PLC_Port,
                Slave_ID = excluded.Slave_ID;
        END;
        """
    )


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

                # Also register every explicitly identified PLCReader in the
                # existing company flow. This makes old saved flows compatible
                # with the multi-PLC model without changing historical PLC IDs.
                _sync_flow_plcs(conn, row["CompanyID"], row["FlowJson"])

        if "FlowTriggerState" in tables:
            cols=_table_columns(conn,"FlowTriggerState")
            if "PLC_ID" not in cols:
                conn.execute("ALTER TABLE FlowTriggerState ADD COLUMN PLC_ID INTEGER")
            conn.execute("""UPDATE FlowTriggerState SET PLC_ID=(SELECT MIN(p.PLC_ID) FROM PLCs p WHERE p.CompanyID=FlowTriggerState.CompanyID)
                           WHERE PLC_ID IS NULL AND 1=(SELECT COUNT(*) FROM PLCs p2 WHERE p2.CompanyID=FlowTriggerState.CompanyID)""")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_flow_trigger_state_company_plc_register ON FlowTriggerState(CompanyID,PLC_ID,TriggerRegister)")

        # Future Flow INSERT/UPDATE operations synchronize PLCs automatically.
        if "Flows" in tables and "PLCs" in tables:
            _create_flow_plc_triggers(conn)

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
