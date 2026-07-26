from src.storage.db import get_connection, load_all_records 
from src.entities.linking import link_entities 

def main(): 
    conn = get_connection() 
    records = load_all_records(conn) 

    clusters = link_entities(records) 

    print(f"{len(records)} grouped into {len(clusters)} clusters") 
    for idx, cluster in enumerate(clusters): 
        print(f"--Entity {(idx+1)} {len(cluster)} records")
        for r in cluster: 
            print(f"[{r['source']}] {r['title']} ({r["timestamp"]})")
        print() 

if __name__ == '__main__':
    main() 