-- Revises: V8
-- Creation Date: 2023-01-13 17:43:53.901805 UTC
-- Reason: open collective sync

CREATE TABLE IF NOT EXISTS open_collective_sync (
  id BIGINT PRIMARY KEY,  -- the discord user ID
  name TEXT NOT NULL,  -- the open collective account name, at time of sync
  slug TEXT NOT NULL,  -- the open collective slug, at time of sync
  account_id TEXT NOT NULL,  -- the open collective account ID
  refresh_token TEXT NOT NULL, -- the Discord refresh token
  access_token TEXT NOT NULL, -- the Discord access token
  expires_at TIMESTAMP NOT NULL  -- the time the access token expires
);

CREATE INDEX IF NOT EXISTS open_collective_sync_account_id_idx ON open_collective_sync (account_id);
