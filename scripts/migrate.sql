-- 1. Add new column for hashed API keys
ALTER TABLE applications
ADD COLUMN api_key_hash VARCHAR(255);

-- 2. Delete all existing data from applications (Cascades to requests/extractions if configured, otherwise might need manual cleanup if foreign keys restrict it. Assuming we delete everything for a fresh start).
-- If there are foreign key constraints, we might need to delete from child tables first.
DELETE FROM usage_logs;
DELETE FROM extractions;
DELETE FROM requests;
DELETE FROM applications;

-- 3. Make the new column required and unique
ALTER TABLE applications
ALTER COLUMN api_key_hash SET NOT NULL,
ADD CONSTRAINT applications_api_key_hash_key UNIQUE (api_key_hash);

-- Create index on the new column
CREATE INDEX ix_applications_api_key_hash ON applications (api_key_hash);

-- 4. Drop the old api_key column
DROP INDEX IF EXISTS ix_applications_api_key;
ALTER TABLE applications DROP COLUMN api_key;

-- 5. Insert ADMIN key
-- SHA256 of "12345" is 5994471abb01112afcc18159f6cc74b4f511b99806da59b3caf5a9c173cacfc5
INSERT INTO applications (id, name, api_key_hash, active, created_at)
VALUES (
    gen_random_uuid(),
    'ADMIN',
    '5994471abb01112afcc18159f6cc74b4f511b99806da59b3caf5a9c173cacfc5',
    true,
    now()
);

-- 6. Enable Row Level Security (RLS) on all tables
ALTER TABLE applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_logs ENABLE ROW LEVEL SECURITY;

-- 7. Create deny-all policies for public access (Supabase defaults to deny-all if RLS is enabled and no policies exist, but explicit is better)
CREATE POLICY deny_all_applications ON applications FOR ALL USING (false);
CREATE POLICY deny_all_requests ON requests FOR ALL USING (false);
CREATE POLICY deny_all_extractions ON extractions FOR ALL USING (false);
CREATE POLICY deny_all_usage_logs ON usage_logs FOR ALL USING (false);

-- Note: Since FastAPI connects using the postgres service role or a dedicated role that bypasses RLS,
-- or if it connects as a regular user, you might need to grant it bypassrls. 
-- Assuming standard Supabase setup where backend uses service_role key, it bypasses RLS automatically.
