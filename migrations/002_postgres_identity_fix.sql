-- PostgreSQL-only id generation hardening for monitoring tables.
-- Safe to run repeatedly; does not drop or truncate data.

CREATE SEQUENCE IF NOT EXISTS bot_runs_id_seq;
ALTER TABLE IF EXISTS bot_runs ALTER COLUMN id TYPE BIGINT;
ALTER TABLE IF EXISTS bot_runs ALTER COLUMN id SET DEFAULT nextval('bot_runs_id_seq');
ALTER TABLE IF EXISTS bot_runs ALTER COLUMN id SET NOT NULL;
ALTER SEQUENCE bot_runs_id_seq OWNED BY bot_runs.id;
SELECT setval(
    'bot_runs_id_seq',
    GREATEST(COALESCE((SELECT MAX(id) FROM bot_runs), 0) + 1, 1),
    false
)
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'bot_runs');

CREATE SEQUENCE IF NOT EXISTS signal_snapshots_id_seq;
ALTER TABLE IF EXISTS signal_snapshots ALTER COLUMN id TYPE BIGINT;
ALTER TABLE IF EXISTS signal_snapshots ALTER COLUMN id SET DEFAULT nextval('signal_snapshots_id_seq');
ALTER TABLE IF EXISTS signal_snapshots ALTER COLUMN id SET NOT NULL;
ALTER SEQUENCE signal_snapshots_id_seq OWNED BY signal_snapshots.id;
SELECT setval(
    'signal_snapshots_id_seq',
    GREATEST(COALESCE((SELECT MAX(id) FROM signal_snapshots), 0) + 1, 1),
    false
)
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'signal_snapshots');

CREATE SEQUENCE IF NOT EXISTS paper_account_snapshots_id_seq;
ALTER TABLE IF EXISTS paper_account_snapshots ALTER COLUMN id TYPE BIGINT;
ALTER TABLE IF EXISTS paper_account_snapshots ALTER COLUMN id SET DEFAULT nextval('paper_account_snapshots_id_seq');
ALTER TABLE IF EXISTS paper_account_snapshots ALTER COLUMN id SET NOT NULL;
ALTER SEQUENCE paper_account_snapshots_id_seq OWNED BY paper_account_snapshots.id;
SELECT setval(
    'paper_account_snapshots_id_seq',
    GREATEST(COALESCE((SELECT MAX(id) FROM paper_account_snapshots), 0) + 1, 1),
    false
)
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'paper_account_snapshots');

CREATE SEQUENCE IF NOT EXISTS sanitized_order_events_id_seq;
ALTER TABLE IF EXISTS sanitized_order_events ALTER COLUMN id TYPE BIGINT;
ALTER TABLE IF EXISTS sanitized_order_events ALTER COLUMN id SET DEFAULT nextval('sanitized_order_events_id_seq');
ALTER TABLE IF EXISTS sanitized_order_events ALTER COLUMN id SET NOT NULL;
ALTER SEQUENCE sanitized_order_events_id_seq OWNED BY sanitized_order_events.id;
SELECT setval(
    'sanitized_order_events_id_seq',
    GREATEST(COALESCE((SELECT MAX(id) FROM sanitized_order_events), 0) + 1, 1),
    false
)
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'sanitized_order_events');
