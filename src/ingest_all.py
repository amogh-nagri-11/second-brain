"""Manual sync entry point: python -m src.ingest_all

Incremental by default -- pass --full to re-fetch and re-embed everything from
scratch (useful after changing the embedding model).
"""

import os
import sys

from src.storage.db import DB_PATH, get_connection
from src.sync import run_sync


def main():
    print("DB path:", os.path.abspath(DB_PATH))

    conn = get_connection()
    try:
        if "--full" in sys.argv:
            print("Full re-ingest: clearing sync cursors and stored records")
            conn.execute("DELETE FROM sync_state")
            conn.execute("DELETE FROM activity_records")
            conn.commit()

        run_sync(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
