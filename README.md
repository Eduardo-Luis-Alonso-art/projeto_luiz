# projeto_luiz

Rode no banco, 1 de cada vez:
```sql

SELECT 'DROP INDEX CONCURRENTLY IF EXISTS '
       || quote_ident(n.nspname) || '.' || quote_ident(c.relname) || ';' AS drop_cmd
FROM   pg_index i
JOIN   pg_class c       ON c.oid = i.indexrelid
JOIN   pg_class t       ON t.oid = i.indrelid
JOIN   pg_namespace n   ON n.oid = t.relnamespace
LEFT   JOIN pg_constraint con ON con.conindid = i.indexrelid
WHERE  n.nspname NOT IN ('pg_catalog','information_schema')
  AND  con.oid IS NULL
  AND  t.relname IN ('estabelecimento','empresa','cnae')
ORDER BY 1;

```
Roda esse para liberar algumas funcionalidades de performance
```sql
CREATE EXTENSION IF NOT EXISTS unaccent;
```

depois, dessa vez 1 por 1

```sql

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_est_cnpj_trgm
  ON estabelecimento USING gin ( (cnpj::text) gin_trgm_ops );

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_est_nome_fantasia_trgm
  ON estabelecimento USING gin ( (nome_fantasia::text) gin_trgm_ops );

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_est_cnpj_eq
  ON estabelecimento (cnpj);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_est_uf
  ON estabelecimento (uf);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_est_situacao
  ON estabelecimento (situacao_cadastral);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_est_cnae_principal
  ON estabelecimento (cnae_fiscal_principal);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_est_cnpj_basico
  ON estabelecimento (cnpj_basico);


CREATE STATISTICS IF NOT EXISTS stx_est_uf_cnae (ndistinct)
  ON uf, cnae_fiscal_principal FROM estabelecimento;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_emp_razao_trgm
  ON empresa USING gin ( (razao_social::text) gin_trgm_ops );

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_emp_razao_ord
  ON empresa (razao_social);


CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_emp_porte
  ON empresa (porte_empresa);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_emp_capital
  ON empresa (capital_social);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_emp_cnpj_basico
  ON empresa (cnpj_basico);

CREATE STATISTICS IF NOT EXISTS stx_emp_porte_capital (dependencies)
  ON porte_empresa, capital_social FROM empresa;


CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_codigo_normalizado
  ON cnae ( (regexp_replace(codigo::text, '\D', '', 'g')) );

```

e por fim, estes:

```sql

ANALYZE estabelecimento;
ANALYZE empresa;
ANALYZE cnae;

ALTER TABLE estabelecimento ALTER COLUMN cnae_fiscal_principal SET STATISTICS 1000;
ALTER TABLE estabelecimento ALTER COLUMN uf SET STATISTICS 1000;
ALTER TABLE estabelecimento ALTER COLUMN situacao_cadastral SET STATISTICS 1000;
ALTER TABLE empresa         ALTER COLUMN porte_empresa SET STATISTICS 1000;
ALTER TABLE empresa         ALTER COLUMN capital_social SET STATISTICS 1000;

ANALYZE estabelecimento;
ANALYZE empresa;

```