"""Non-secret identity for a read-only observer across supervisor restarts."""
import hashlib
import json
from ..storage.sqlite_url import sqlite_url_to_path


def observer_identity(cfg):
    database = str(sqlite_url_to_path(cfg.storage.url).resolve()) if cfg.storage.url.startswith('sqlite:///') else cfg.storage.url
    facts = {'account': cfg.account_id, 'database': database, 'mode': cfg.mode,
             'host': cfg.api.host, 'port': cfg.api.port, 'universe': cfg.universe,
             't0': {key: str(value) for key, value in cfg.t0.__dict__.items()}}
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
