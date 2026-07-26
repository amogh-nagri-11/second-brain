from datetime import timezone, timedelta, datetime
from src.ingestion.github import fetch_recent_commits 
from src.ingestion.calendar import fetch_recent_events
from src.embeddings.provider import EmbeddingsProvider 
from src.storage.db import get_connection, save_record 

def main(): 
    import os
    print("DB path:", os.path.abspath("second_brain.db"))       

    since = datetime.now(timezone.utc) - timedelta(days=90) 

    github_records = fetch_recent_commits("amogh-nagri-11", "second-brain", since) 
    cal_records = fetch_recent_events(since) 
    all_records = github_records + cal_records 

    print(f"Ingested {len(all_records)} total records") 

    embeddor = EmbeddingsProvider() 
    conn = get_connection() 

    for record in all_records: 
        text_to_embed = f"{record.title}\n{record.body}" 
        embedding = embeddor.embed(text_to_embed) 
        save_record(conn, record, embedding) 
        print(f"stored + embedded {record.title}")

    conn.close() 

if __name__ == "__main__": 
    main()

