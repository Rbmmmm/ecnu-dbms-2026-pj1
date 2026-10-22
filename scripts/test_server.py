import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings, guard_test_database
from app.main import create_app


def factory():
    uri, database, run = [os.environ[key] for key in ['SURVEY_TEST_URI', 'SURVEY_TEST_DB', 'SURVEY_TEST_RUN_ID']]
    guard_test_database(uri, database, run)
    return create_app(Settings(uri, database, os.environ['SURVEY_TEST_ORIGIN'], testing=True, run_id=run))
