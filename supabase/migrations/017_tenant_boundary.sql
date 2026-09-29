-- v2.1: backend-only database boundary.
-- Social Saver clients (Telegram, future Lovable/Mini App) talk to our backend,
-- never directly to the Supabase Data API. Prevent accidental public exposure.
revoke all privileges on all tables in schema public from anon, authenticated;
revoke all privileges on all sequences in schema public from anon, authenticated;
revoke execute on all functions in schema public from anon, authenticated;

-- Best effort for objects created by later migrations under the migration role.
alter default privileges in schema public revoke all on tables from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke execute on functions from anon, authenticated;

comment on schema public is
  'Social Saver backend-only schema. Browser/Mini App access must go through the authenticated application API.';
