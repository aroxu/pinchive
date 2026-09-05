def test_legacy_worker_import_path_points_to_current_settings():
    from app.tasks import WorkerSettings as LegacyWorkerSettings
    from app.worker import WorkerSettings

    assert LegacyWorkerSettings is WorkerSettings
