import os
from dataclasses import dataclass
from pathlib import Path
from pymongo.uri_parser import parse_uri

ROOT = Path(__file__).resolve().parents[1]
LIMITS = {'body_bytes': 1024 * 1024, 'document_bytes': 8 * 1024 * 1024,
          'questions': 100, 'options': 100, 'rules': 50, 'question_title': 1000,
          'option_text': 200, 'survey_title': 200, 'description': 5000, 'text_answer': 10000,
          'revision': 2**31-1, 'offset': 2**63-1}


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    mongo_uri: str
    database: str
    origin: str = 'http://127.0.0.1:8000'
    secure_cookie: bool = False
    testing: bool = False
    run_id: str = ''
    max_body: int = LIMITS['body_bytes']

    @classmethod
    def from_env(cls):
        load_env()
        return cls(os.environ['SURVEY_MONGO_URI'], os.environ['SURVEY_DB'],
                   os.getenv('SURVEY_ORIGIN', 'http://127.0.0.1:8000'),
                   os.getenv('SURVEY_SECURE_COOKIE', 'false') == 'true')


def guard_test_database(uri, database, run_id):
    import re
    if not re.fullmatch(r'[a-z0-9]{8,40}', run_id):
        raise ValueError('invalid test run ID')
    parsed = parse_uri(uri)
    if parsed['username'] or parsed['database'] or parsed['options']:
        raise ValueError('test URI must be a plain local connection')
    if parsed['nodelist'] not in [[('127.0.0.1', 27017)], [('localhost', 27017)]]:
        raise ValueError('tests require local MongoDB on port 27017')
    if database != 'survey_test_' + run_id:
        raise ValueError('refusing to use or clean a non-run test database')
