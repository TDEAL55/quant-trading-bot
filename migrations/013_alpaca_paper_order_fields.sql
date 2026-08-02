ALTER TABLE paper_orders ADD COLUMN client_order_id TEXT;
ALTER TABLE paper_orders ADD COLUMN requested_quantity REAL;
ALTER TABLE paper_orders ADD COLUMN broker_backend TEXT;
ALTER TABLE paper_orders ADD COLUMN order_type TEXT;
ALTER TABLE paper_orders ADD COLUMN time_in_force TEXT;
ALTER TABLE paper_orders ADD COLUMN broker_updated_at TEXT;
ALTER TABLE paper_orders ADD COLUMN rejection_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_paper_orders_client_order_id ON paper_orders(client_order_id);
CREATE INDEX IF NOT EXISTS idx_paper_orders_broker_backend ON paper_orders(broker_backend);
