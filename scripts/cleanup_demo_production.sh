#!/usr/bin/env bash
# Purge completed manufacturing orders created by the AI-agent production
# demo. Completed MOs own finished stock moves and cannot be unlinked
# through the ORM — this helper removes them on the DEMO database only,
# by the explicit demo origin marker.
#
# Usage (inside the WSL distro, as user erp):
#   bash scripts/cleanup_demo_production.sh
set -e

DB=erp19

echo "[cleanup] purging demo manufacturing orders (origin='AI-PRODDEMO') from $DB ..."

env PGPASSWORD=odoo psql -U odoo -h localhost -d "$DB" <<'SQL'
-- stock moves of demo MOs (and their picking associations)
DELETE FROM stock_move_line
WHERE move_id IN (SELECT id FROM stock_move WHERE production_id IN (
    SELECT id FROM mrp_production WHERE origin = 'AI-PRODDEMO'));
DELETE FROM stock_move
WHERE production_id IN (SELECT id FROM mrp_production WHERE origin = 'AI-PRODDEMO');

-- production order lines / workorders
DELETE FROM mrp_workorder WHERE production_id IN (SELECT id FROM mrp_production WHERE origin = 'AI-PRODDEMO');

-- the MOs themselves
DELETE FROM mrp_production WHERE origin = 'AI-PRODDEMO';

-- demo BOM (汉堡 = 可乐 × 2)
DELETE FROM mrp_bom_line WHERE bom_id IN (SELECT id FROM mrp_bom WHERE code = 'AI-PRODDEMO-BOM');
DELETE FROM mrp_bom WHERE code = 'AI-PRODDEMO-BOM';

SELECT 'demo MOs remaining' AS what, count(*) AS n FROM mrp_production WHERE origin = 'AI-PRODDEMO'
UNION ALL
SELECT 'demo BOMs remaining', count(*) FROM mrp_bom WHERE code = 'AI-PRODDEMO-BOM';
SQL

echo "[cleanup] done."
