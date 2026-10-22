import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn

if __name__ == '__main__':
    uvicorn.run('app.main:create_app', factory=True, host='127.0.0.1', port=8000, access_log=False)
