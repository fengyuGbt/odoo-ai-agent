# Extra add-ons for the docker-compose stack

This directory is mounted read-only at `/mnt/extra-addons` in the Odoo
container. It starts empty.

## Optional: enable rule precheck with OCA tier validation

The action gate (`agent/gate.py`) already guarantees that the AI cannot
complete any write action on its own — every create/write/unlink/call
waits for a human. Rule precheck (`agent/tier_precheck.py`) is an
enhancement that reads OCA tier definitions. To enable it:

1. Get the modules (Odoo 19 has no tier-validation branch yet, use the
   18.0 branches and bump their manifest versions):

   ```bash
   git clone https://gh-proxy.com/https://github.com/OCA/server-ux -b 18.0
   git clone https://gh-proxy.com/https://github.com/OCA/sale-workflow -b 18.0
   git clone https://gh-proxy.com/https://github.com/OCA/purchase-workflow -b 18.0
   ```

2. Copy into this directory:

   ```
   addons/base_tier_validation/
   addons/sale_tier_validation/
   addons/purchase_tier_validation/
   ```

3. Install them (Apps menu → Update Apps List → search "tier") or add
   them to the `-i` list of the `odoo` service in `docker-compose.yml`.

The stack works fully without this step.
