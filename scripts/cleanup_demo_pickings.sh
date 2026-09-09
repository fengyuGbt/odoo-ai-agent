#!/usr/bin/env bash
# Purge completed (done) stock pickings created by the AI-agent demos.
#
# Odoo refuses to delete done transfers through the ORM (real stock
# semantics: create a return instead). This script is a maintenance
# helper for the DEMO database only — it removes done pickings whose
# origin looks like a demo purchase (P...) or demo sale (S...) order
# and whose id is above the known baseline.
#
# Usage (inside the WSL distro, as user erp):
#   bash scripts/cleanup_demo_pickings.sh
set -e

DB=erp19
BASELINE_ID=7

echo "[cleanup] purging demo completed pickings (id > $BASELINE_ID, origin LIKE 'P%'/'S%', state='done') from $DB ..."

env PGPASSWORD=odoo psql -U odoo -h localhost -d "$DB" <<'SQL'
DELETE FROM stock_move_line
WHERE picking_id IN (
    SELECT id FROM stock_picking
    WHERE id > 7 AND (origin LIKE 'P%' OR origin LIKE 'S%') AND state = 'done'
);
DELETE FROM stock_move
WHERE picking_id IN (
    SELECT id FROM stock_picking
    WHERE id > 7 AND (origin LIKE 'P%' OR origin LIKE 'S%') AND state = 'done'
);
DELETE FROM stock_picking
WHERE id > 7 AND (origin LIKE 'P%' OR origin LIKE 'S%') AND state = 'done';
SELECT count(*) AS remaining_pickings FROM stock_picking;
SQL

echo "[cleanup] done."
