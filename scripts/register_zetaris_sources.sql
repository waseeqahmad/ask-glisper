-- Zetaris Lightning SQL, run by scripts/register_zetaris.py (placeholders filled from env, never committed).
-- One Postgres source holding the synthetic demo audit; its three tables become GLISPER_DEMO.answers etc.
CREATE DATASOURCE {catalog} OPTIONS (jdbcdriver "org.postgresql.Driver", jdbcurl "{jdbcurl}",
  username "{user}", password "{password}", fetchsize "1000"{extra});
REGISTER DATASOURCE TABLES FROM {catalog}
