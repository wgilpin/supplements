import json
import logging
from pathlib import Path
from skg.fetch import efetch
from skg import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill")

def main():
    abstracts_dir = config.ABSTRACTS_DIR
    json_files = list(abstracts_dir.glob("*.json"))
    logger.info(f"Found {len(json_files)} cached abstract files.")
    
    missing_pmids = []
    loaded_data = {}
    for path in json_files:
        try:
            data = json.loads(path.read_text())
            # check if fields are missing (we consider them missing if "journal" is not in dict)
            if "journal" not in data or "authors" not in data:
                missing_pmids.append(data["pmid"])
                loaded_data[data["pmid"]] = data
        except Exception as e:
            logger.error(f"Error reading {path}: {e}")
            
    if not missing_pmids:
        logger.info("All files already have journal and authors fields. No backfill needed.")
        return
        
    logger.info(f"Found {len(missing_pmids)} files missing fields. Fetching from PubMed in batches...")
    
    # Fetch in batches of 50
    batch_size = 50
    for i in range(0, len(missing_pmids), batch_size):
        batch = missing_pmids[i:i+batch_size]
        logger.info(f"Fetching batch {i // batch_size + 1}: {len(batch)} PMIDs...")
        try:
            fetched = efetch(batch)
            fetched_map = {rec["pmid"]: rec for rec in fetched}
            
            for pmid in batch:
                path = abstracts_dir / f"{pmid}.json"
                orig = loaded_data.get(pmid)
                if not orig:
                    continue
                fetched_rec = fetched_map.get(pmid)
                if fetched_rec:
                    orig["journal"] = fetched_rec["journal"]
                    orig["authors"] = fetched_rec["authors"]
                else:
                    orig["journal"] = ""
                    orig["authors"] = ""
                path.write_text(json.dumps(orig, indent=2))
                
        except Exception as e:
            logger.error(f"Error fetching batch: {e}")
            
    logger.info("Backfill completed successfully.")

if __name__ == "__main__":
    main()
