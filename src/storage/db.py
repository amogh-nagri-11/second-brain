import sqlite3 
import json 
import numpy as np 

DB_PATH="second-brain.db" 

def get_connection() -> sqlite3.Connection: 
    conn = sqlite3.connect(DB_PATH) 
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_records ( 
            id TEXT PRIMARY KEY, 
            source TEXT NOT NULL, 
            timestamp TEXT NOT NULL, 
            title TEXT NOT NULL, 
            body TEXT NOT NULL, 
            url TEXT, 
            raw TEXT NOT NULL, 
            embedding BLOB NOT NULL
        )
    """) 

    return conn 

def save_record(conn: sqlite3.Connection, record, embeddings: list[float]): 
    embeddings_bytes = np.array(embeddings, dtype=np.float32).tobytes() 

    conn.execute(
        """
            INSERT OR REPLACE INTO activity_records
            (id, source, timestamp, title, body, url, raw, embedding)
            VALUES (?,?,?,?,?,?,?,?)
        """,
        ( 
            record.id, record.source, record.timestamp, record.title, record.body, record.url, json.dumps(record.raw), embeddings_bytes 
        )
    ) 
    conn.commit() 

def load_all_records(conn: sqlite3.Connection): 
    rows = conn.execute("SELECT * FROM activity_records").fetchall() 
    results=[] 
    for row in rows: 
        embeddings = np.frombuffer(row[7], dtype=np.float32) 
        results.append({
            "id": row[0], 
            "source": row[1], 
            "timestamp": row[2], 
            "title": row[3], 
            "body": row[4], 
            "url": row[5], 
            "raw": json.loads(row[6]), 
            "embedding": embeddings
        }); 
    return results