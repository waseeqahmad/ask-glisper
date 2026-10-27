-- Ask Glisper's read-only window onto a Glisper database: three flat views in their own schema, one row set
-- per audit. Zetaris registers this schema as the GLISPER source; the agent's run_sql() scopes every query to
-- one audit_id in code, so a question about one brand can never read another brand's rows.
--   psql "$GLISPER_ADMIN_DB_URL" -v ro_password="..." -f scripts/glisper_views.sql
-- Glisper's statuses are kept as-is: won = brand named in the answer, partial = only our site cited, lost = missing.
CREATE SCHEMA IF NOT EXISTS ask_glisper;

CREATE OR REPLACE VIEW ask_glisper.answers AS
SELECT pr.id::text AS result_id, p.audit_id::text AS audit_id, CAST(r.started_at AS date) AS run_date,
       pr.prompt_id::text AS prompt_id, p.text AS prompt, pr.engine_slug AS engine, pr.status,
       pr.position AS our_position,
       CASE WHEN pr.position = 1 THEN b.name ELSE c.name END AS winner,  -- brand ranked first in the answer
       pr.raw_answer AS answer_text, pr.capture_method
FROM public.prompt_results pr
JOIN public.prompts p ON p.id = pr.prompt_id
JOIN public.brands b ON b.id = p.brand_id
JOIN public.runs r ON r.id = pr.run_id
LEFT JOIN public.competitors c ON c.id = pr.winning_competitor_id
WHERE pr.status IN ('won', 'partial', 'lost');

CREATE OR REPLACE VIEW ask_glisper.answer_sources AS
SELECT a.result_id, a.audit_id, a.run_date, a.engine, a.status, s.domain, s.type AS source_type, rs.url
FROM ask_glisper.answers a
JOIN public.prompt_result_sources rs ON rs.result_id::text = a.result_id
JOIN public.sources s ON s.id = rs.source_id;

CREATE OR REPLACE VIEW ask_glisper.answer_mentions AS
SELECT a.result_id, a.audit_id, a.run_date, a.engine, a.status, c.name AS brand, false AS is_us, m.position
FROM ask_glisper.answers a
JOIN public.prompt_result_mentions m ON m.result_id::text = a.result_id
JOIN public.competitors c ON c.id = m.competitor_id
UNION ALL
SELECT a.result_id, a.audit_id, a.run_date, a.engine, a.status, b.name, true, a.our_position
FROM ask_glisper.answers a
JOIN public.brands b ON b.audit_id::text = a.audit_id
WHERE a.our_position IS NOT NULL;

-- one row per brand: what the agent needs to know before it plans (name, site, audit, how the answers were captured)
CREATE OR REPLACE VIEW ask_glisper.brand_profiles AS
SELECT b.id::text AS brand_id, b.audit_id::text AS audit_id, b.workspace_id::text AS workspace_id, b.name AS brand,
       b.website,
       (SELECT string_agg(DISTINCT a.capture_method, ',') FROM ask_glisper.answers a
        WHERE a.audit_id = b.audit_id::text) AS capture_methods
FROM public.brands b;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ask_glisper_ro') THEN
    CREATE ROLE ask_glisper_ro LOGIN;
  END IF;
END $$;
ALTER ROLE ask_glisper_ro PASSWORD :'ro_password';
GRANT USAGE ON SCHEMA ask_glisper TO ask_glisper_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA ask_glisper TO ask_glisper_ro;
ALTER ROLE ask_glisper_ro SET search_path = ask_glisper;
