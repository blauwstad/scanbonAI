-- Migration 005: Add source column to webhook_events for OpenClaw support
ALTER TABLE webhook_events ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'whatsapp';
