import os


def test_batch_config_defaults(monkeypatch):
    for k in ["MEDGO_MAX_CONCURRENCY", "BATCH_ARCHIVE_MAX_SIZE", "BATCH_CHUNK_SIZE",
              "BATCH_SWEEP_INTERVAL", "BULK_WINDOW_START", "BULK_WINDOW_END",
              "BATCH_FILE_MAX_SIZE", "DEAD_LETTER_TTL"]:
        monkeypatch.delenv(k, raising=False)
    from app.config import Settings
    s = Settings()
    assert s.MEDGO_MAX_CONCURRENCY == 2
    assert s.BATCH_ARCHIVE_MAX_SIZE == 10737418240
    assert s.BATCH_CHUNK_SIZE == 5242880
    assert s.BATCH_SWEEP_INTERVAL == 300
    assert s.BULK_WINDOW_START == 22
    assert s.BULK_WINDOW_END == 8
    assert s.BATCH_FILE_MAX_SIZE == 52428800
    assert s.DEAD_LETTER_TTL == 604800