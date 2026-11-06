# OdooWoo Helper for Odoo 20

Adds WooCommerce references to sales, invoices and deliveries; RPC methods for
order confirmation, invoicing, payments, cancellation and delivery completion;
SKU pricing; and stock, price and order-completion webhooks.

This `v20` branch targets Odoo Community 20.0. The original code remains on `main`.
Install the addon directory as **`odoowoo_helper`**, the technical name checked by
OdooWoo. Dependencies are `sale_stock`, `account` and Python `requests`.

## Install

Clone branch `v20` into an addons directory named `odoowoo_helper`, then install
OdooWoo Helper from Apps, or run:

```sh
odoo -d DATABASE -i odoowoo_helper --stop-after-init
```

Restart the normal Odoo service after installation. For later updates, use
`-u odoowoo_helper` with the same database and restart.

## Webhooks

OdooWoo's API-key setup supplies these system parameters:

| Parameter | Purpose |
| --- | --- |
| `webhook_api_key` | Shared secret sent to the middleware |
| `webhook_stock_update` | Stock notification URL |
| `webhook_price_update` | Price notification URL |
| `webhook_change_status` | Completed-order notification URL |
| `webhook_stock_locations` | Optional comma-separated internal location IDs |

Unset URLs disable their notifications. Location reporting defaults to the
current company's warehouse stock locations. The existing payload formats are
preserved, including per-location stock and pricelist IDs. A legacy `set_param`
RPC adapter lets the existing OdooWoo setup write webhook strings through Odoo
20's `set_str`, with the caller's normal permissions.

Notifications run after commit. Stock and price events coalesce within a
transaction. Stock requests retry three times; completion is marked sent only
after a successful HTTP response. Delivery is best effort, with failures logged;
there is no persistent job queue or guaranteed delivery across worker restarts.
Secrets are not logged in payloads.

## Odoo 20 changes

- Current stock quantities, units and `picked` flags replace removed fields.
  Automatic delivery completion leaves shortages open and follows new transfers
  created during a multi-step delivery. It never forces stock or cancels shortages.
- Native picking completion is retained. PO receipt reservation is restricted to
  fully invoiced, paid sales as in the original helper. Explicit reservation RPCs
  remain available to the middleware.
- Payments use the recordset-returning wizard API, retain references on payments,
  support partial payments and refunds, and roll back on a reported failure.
  Overpayments beyond the existing 0.015 rounding tolerance are refused.
- Stored custom stock totals become computed values so location context and
  stock changes are reflected when read. Their existing calculation is retained:
  on-hand minus outgoing quantity, rather than forecast or unreserved quantity.
- Batch creation, current product types, typed configuration and
  `models.Constraint` replace older APIs. WooCommerce order IDs remain unique.
- Resetting an open delivery releases reservations through core stock methods.
  The resulting state is computed by Odoo rather than forced to `waiting`.

## Verification

Tested on Odoo **20.0-20261102**, including clean installation, inherited views,
order/invoice dates, partial and refund payments, error rollback, duplicate-order
protection, shortages, converted units, two-step deliveries, receipt reservation,
SKU pricing and notification preparation.

Run the module tests in a separate database:

```sh
odoo -d TEST_DATABASE -u odoowoo_helper --test-enable \
  --test-tags=/odoowoo_helper --stop-after-init --workers=0 --max-cron-threads=0
```

Historical code in `models/backup` is not imported or used by this branch.
