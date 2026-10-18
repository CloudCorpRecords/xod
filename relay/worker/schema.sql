-- Xod Post Office (Cloudflare D1). Same data as relay/server.py.
CREATE TABLE IF NOT EXISTS names (name TEXT PRIMARY KEY, sign_pub TEXT NOT NULL, box_pub TEXT NOT NULL,
                                  registered_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS mail (id TEXT PRIMARY KEY, name TEXT NOT NULL, envelope TEXT NOT NULL, received_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS mail_name ON mail(name, received_at);
-- Self-host directory: public data only (address -> endpoint URL + public keys). Re-run this
-- file against an existing database after deploying; the IF NOT EXISTS makes it safe.
CREATE TABLE IF NOT EXISTS directory (address TEXT PRIMARY KEY, endpoint TEXT NOT NULL,
                                      sign_pub TEXT NOT NULL, box_pub TEXT NOT NULL, status TEXT NOT NULL,
                                      challenge_id TEXT, challenge TEXT,
                                      registered_at REAL NOT NULL, last_seen REAL NOT NULL);
