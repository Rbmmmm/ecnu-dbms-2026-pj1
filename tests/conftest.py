import os
import sys
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings, guard_test_database
from app.main import create_app

ORIGIN = 'http://127.0.0.1:8000'
HEADERS = {'Origin': ORIGIN, 'X-Survey-Request': '1'}


@pytest.fixture(scope='session')
def settings():
    run = os.getenv('SURVEY_TEST_RUN_ID', uuid4().hex)
    uri = os.getenv('SURVEY_TEST_URI', 'mongodb://127.0.0.1:27017/')
    name = os.getenv('SURVEY_TEST_DB', 'survey_test_' + run)
    guard_test_database(uri, name, run)
    return Settings(uri, name, ORIGIN, testing=True, run_id=run)


@pytest.fixture
def context(settings):
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN, headers=HEADERS) as client:
        db = app.state.db
        guard_test_database(settings.mongo_uri, settings.database, settings.run_id)
        for name in ['users', 'surveys', 'responses', 'sessions']:
            db['survey_' + name].delete_many({})
        yield app, client, db


@pytest.fixture(scope='session', autouse=True)
def cleanup(settings):
    yield
    guard_test_database(settings.mongo_uri, settings.database, settings.run_id)
    with MongoClient(settings.mongo_uri) as client:
        client.drop_database(settings.database)
