from __future__ import annotations


import json

import sqlite3

from datetime import datetime

from pathlib import Path


from services.agent_runtime.app.observability.models import (
    TraceEvent,
    TraceSpan,
)



def json_serializer(obj):
    """
    JSON serializer for trace data.
    """


    if isinstance(
        obj,
        datetime,
    ):

        return obj.isoformat()



    if hasattr(
        obj,
        "value",
    ):

        return obj.value



    if hasattr(
        obj,
        "model_dump",
    ):

        return obj.model_dump()



    return str(obj)





class TraceStore:
    """
    Trace persistence abstraction.

    Current:
    SQLite storage.

    Future:
    - PostgreSQL
    - ClickHouse
    - OpenTelemetry backend
    """



    def __init__(self):

        self.db_path = (
            Path("data")
            /
            "traces.db"
        )


        self.db_path.parent.mkdir(
            exist_ok=True
        )


        self._init_db()



    def _connect(self):

        return sqlite3.connect(
            self.db_path
        )



    def _init_db(
        self,
    ) -> None:


        with self._connect() as conn:


            conn.execute(

                """
                CREATE TABLE IF NOT EXISTS traces
                (
                    trace_id TEXT PRIMARY KEY,

                    agent TEXT NOT NULL,

                    success INTEGER,

                    score REAL,

                    message TEXT,

                    start_time TEXT,

                    end_time TEXT,

                    duration_ms REAL,

                    input_data TEXT,

                    output_data TEXT,

                    metadata TEXT
                )
                """

            )


            conn.execute(

                """
                CREATE TABLE IF NOT EXISTS trace_spans
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    trace_id TEXT NOT NULL,

                    type TEXT NOT NULL,

                    name TEXT NOT NULL,

                    start_time TEXT,

                    end_time TEXT,

                    duration_ms REAL,

                    success INTEGER,

                    input_data TEXT,

                    output_data TEXT,

                    error TEXT
                )
                """

            )



    def _connect_db(self):

        return sqlite3.connect(
            self.db_path
        )



    def save(
        self,
        trace: TraceEvent,
    ) -> None:


        with self._connect() as conn:


            conn.execute(

                """
                INSERT OR REPLACE INTO traces
                (
                    trace_id,
                    agent,
                    success,
                    score,
                    message,
                    start_time,
                    end_time,
                    duration_ms,
                    input_data,
                    output_data,
                    metadata
                )

                VALUES
                (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,

                (

                    trace.trace_id,

                    trace.agent,

                    int(
                        trace.success
                    ),

                    trace.score,

                    trace.message,

                    trace.start_time.isoformat(),

                    (
                        trace.end_time.isoformat()
                        if trace.end_time
                        else None
                    ),

                    trace.duration_ms,


                    json.dumps(
                        trace.input_data,
                        ensure_ascii=False,
                        default=json_serializer,
                    ),


                    json.dumps(
                        trace.output_data,
                        ensure_ascii=False,
                        default=json_serializer,
                    ),


                    json.dumps(
                        trace.metadata,
                        ensure_ascii=False,
                        default=json_serializer,
                    ),

                )

            )


            self._save_spans(
                conn,
                trace,
            )



    def _save_spans(
        self,
        conn,
        trace: TraceEvent,
    ) -> None:


        for span in trace.spans:


            conn.execute(

                """
                INSERT INTO trace_spans
                (
                    trace_id,
                    type,
                    name,
                    start_time,
                    end_time,
                    duration_ms,
                    success,
                    input_data,
                    output_data,
                    error
                )

                VALUES
                (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,

                (

                    trace.trace_id,

                    span.type,

                    span.name,

                    span.start_time.isoformat(),

                    (
                        span.end_time.isoformat()
                        if span.end_time
                        else None
                    ),

                    span.duration_ms,

                    int(
                        span.success
                    ),


                    json.dumps(
                        span.input_data,
                        ensure_ascii=False,
                        default=json_serializer,
                    ),


                    json.dumps(
                        span.output_data,
                        ensure_ascii=False,
                        default=json_serializer,
                    ),


                    span.error,

                )

            )



    def _load_spans(
        self,
        conn,
        trace_id: str,
    ) -> list[TraceSpan]:


        rows = conn.execute(

            """
            SELECT

                type,

                name,

                start_time,

                end_time,

                duration_ms,

                success,

                input_data,

                output_data,

                error

            FROM trace_spans

            WHERE trace_id = ?

            ORDER BY id ASC

            """,

            (
                trace_id,
            )

        ).fetchall()



        return [

            TraceSpan(

                type=row[0],

                name=row[1],

                start_time=row[2],

                end_time=row[3],

                duration_ms=row[4],

                success=bool(
                    row[5]
                ),

                input_data=json.loads(
                    row[6]
                ),

                output_data=json.loads(
                    row[7]
                ),

                error=row[8],

            )

            for row

            in rows

        ]



    def update(
        self,
        trace: TraceEvent,
    ) -> None:


        with self._connect() as conn:


            conn.execute(

                """
                UPDATE traces

                SET

                    agent = ?,

                    success = ?,

                    score = ?,

                    message = ?,

                    start_time = ?,

                    end_time = ?,

                    duration_ms = ?,

                    input_data = ?,

                    output_data = ?,

                    metadata = ?

                WHERE trace_id = ?

                """,

                (

                    trace.agent,

                    int(
                        trace.success
                    ),

                    trace.score,

                    trace.message,

                    trace.start_time.isoformat(),

                    (
                        trace.end_time.isoformat()
                        if trace.end_time
                        else None
                    ),

                    trace.duration_ms,


                    json.dumps(
                        trace.input_data,
                        ensure_ascii=False,
                        default=json_serializer,
                    ),


                    json.dumps(
                        trace.output_data,
                        ensure_ascii=False,
                        default=json_serializer,
                    ),


                    json.dumps(
                        trace.metadata,
                        ensure_ascii=False,
                        default=json_serializer,
                    ),


                    trace.trace_id,

                )

            )


            conn.execute(

                """
                DELETE FROM trace_spans

                WHERE trace_id = ?

                """,

                (
                    trace.trace_id,
                )

            )


            self._save_spans(
                conn,
                trace,
            )



    def list(
        self,
    ) -> list[TraceEvent]:


        with self._connect() as conn:


            rows = conn.execute(

                """
                SELECT *

                FROM traces

                ORDER BY rowid DESC

                """

            ).fetchall()



            return [

                self._row_to_model(
                    conn,
                    row,
                )

                for row

                in rows

            ]



    def get(
        self,
        trace_id: str,
    ) -> TraceEvent | None:


        with self._connect() as conn:


            row = conn.execute(

                """
                SELECT *

                FROM traces

                WHERE trace_id = ?

                """,

                (
                    trace_id,
                )

            ).fetchone()



            if not row:

                return None



            return self._row_to_model(
                conn,
                row,
            )



    def _row_to_model(
        self,
        conn,
        row,
    ) -> TraceEvent:


        trace_id = row[0]


        spans = self._load_spans(
            conn,
            trace_id,
        )



        return TraceEvent(

            trace_id=trace_id,

            agent=row[1],

            success=bool(
                row[2]
            ),

            score=row[3],

            message=row[4],

            start_time=row[5],

            end_time=row[6],

            duration_ms=row[7],

            input_data=json.loads(
                row[8]
            ),

            output_data=json.loads(
                row[9]
            ),

            spans=spans,

            metadata=json.loads(
                row[10]
            ),

        )