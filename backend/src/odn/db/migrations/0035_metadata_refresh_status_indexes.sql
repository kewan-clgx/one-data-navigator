CREATE INDEX metadata_refresh_latest ON metadata_refresh_jobs
  (connection_id, connection_revision, created_at DESC, id DESC);

CREATE INDEX metadata_refresh_last_completed ON metadata_refresh_jobs
  (connection_id, connection_revision, finished_at DESC NULLS LAST, created_at DESC, id DESC)
  WHERE status IN ('succeeded', 'partial', 'failed', 'cancelled');
