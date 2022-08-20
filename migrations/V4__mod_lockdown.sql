-- Revises: V3
-- Creation Date: 2022-08-20 23:13:04.392855 UTC
-- Reason: mod lockdown and ignoring multiple entitiy types

ALTER TABLE guild_mod_config RENAME COLUMN safe_automod_channel_ids TO safe_automod_entity_ids;

CREATE TABLE IF NOT EXISTS guild_lockdowns (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    allow BIGINT NOT NULL,
    deny BIGINT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);
