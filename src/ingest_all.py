"""Manual sync entry point: python -m src.ingest_all

Incremental by default -- pass --full to re-fetch and re-embed everything from
scratch (useful after changing the embedding model).
"""

import sys

from src.config.paths import db_path
from src.storage.db import get_connection
from src.sync import run_sync


def main():
    print("DB path:", db_path())

    conn = get_connection()
    try:
        if "--full" in sys.argv:
            print("Full re-ingest: clearing sync cursors and stored records")
            conn.execute("DELETE FROM sync_state")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM items")
            conn.commit()

        run_sync(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
