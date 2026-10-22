import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.db import connect, initialize, inspect_contract

if __name__ == '__main__':
    client, db = connect(Settings.from_env())
    try:
        initialize(db)
        print(inspect_contract(db))
    finally:
        client.close()
