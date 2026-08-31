#!/usr/bin/env python3
"""Read cache-excluded parent-agent token usage from Copilot's session store."""

import json
import sqlite3
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: copilot_usage.py SESSION_STORE_DB SESSION_ID")

    db_path = Path(sys.argv[1])
    session_id = sys.argv[2]
    if not db_path.is_file():
        print("{}")
        return

    try:
        connection = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=0.25,
        )
        rows = connection.execute(
            """
            SELECT turn_index,
                   SUM(
                       MAX(COALESCE(input_tokens, 0)
                           - COALESCE(cache_read_tokens, 0), 0)
                       + COALESCE(output_tokens, 0)
                   )
            FROM assistant_usage_events
            WHERE session_id = ?
              AND turn_index IS NOT NULL
              AND parent_tool_call_id IS NULL
            GROUP BY turn_index
            """,
            (session_id,),
        )
        usage = {
            str(turn_index): tokens
            for turn_index, tokens in rows
            if isinstance(tokens, int) and tokens > 0
        }
    except sqlite3.Error:
        usage = {}
    finally:
        if "connection" in locals():
            connection.close()

    print(json.dumps(usage, separators=(",", ":")))


if __name__ == "__main__":
    main()
