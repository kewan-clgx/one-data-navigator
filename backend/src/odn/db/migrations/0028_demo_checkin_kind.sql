-- Live demo follow-ups (docs/config/config_demo_tenant.md): how each check-in started, for the admin's visitor
-- list and funnel. new = first demo user for the email; returning = same browser (device token); fresh = the email
-- already had demo chats, so a new demo user started and the inbox got a link; link = reopened from that link.
ALTER TABLE demo_checkins
  ADD COLUMN kind text NOT NULL DEFAULT 'new' CHECK (kind IN ('new', 'returning', 'fresh', 'link'));
CREATE INDEX demo_checkins_user ON demo_checkins (user_id);
