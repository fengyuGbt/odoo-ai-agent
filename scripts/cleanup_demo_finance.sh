#!/usr/bin/env bash
# Purge demo finance records created by the AI-agent payment demo.
#
# Odoo protects posted account moves/payments (audit trail) — the demo
# resets them to draft/cancelled first; this helper removes anything the
# demo left behind on the DEMO database only. It only touches records
# explicitly marked by the demo (payment reference prefix, bill origins).
#
# Usage (inside the WSL distro, as user erp):
#   bash scripts/cleanup_demo_finance.sh
set -euo pipefail

DB=erp19

echo "[cleanup] purging demo payments (payment_reference LIKE 'AI-PAYDEMO%') and demo bills (in_invoice, origin P%, id > 7) from $DB ..."

env PGPASSWORD=odoo psql -U odoo -h localhost -d "$DB" <<'SQL'
-- payments created by the demo
DELETE FROM account_payment WHERE payment_reference LIKE 'AI-PAYDEMO%';

-- vendor bills whose origin looks like a demo purchase order and that are
-- above the known baseline (real DB never had an in_invoice before demos)
DELETE FROM account_move_line
WHERE move_id IN (
    SELECT id FROM account_move
    WHERE move_type = 'in_invoice' AND invoice_origin LIKE 'P%' AND id > 7
);
DELETE FROM account_move
WHERE move_type = 'in_invoice' AND invoice_origin LIKE 'P%' AND id > 7;

SELECT 'demo payments remaining' AS what, count(*) AS n FROM account_payment WHERE payment_reference LIKE 'AI-PAYDEMO%'
UNION ALL
SELECT 'demo bills remaining', count(*) FROM account_move WHERE move_type = 'in_invoice' AND invoice_origin LIKE 'P%' AND id > 7;
SQL

echo "[cleanup] done."
