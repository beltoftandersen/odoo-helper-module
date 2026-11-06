"""Run through `odoo shell` in the disposable compatibility database only."""

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from odoo import Command

assert env.cr.dbname == 'odoowoo_helper_v20_qa', 'Disposable QA database required'
events = {}
received = threading.Condition()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with received:
            events[self.path] = payload
            received.notify_all()
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'
reference = 'QA_HTTP_' + uuid.uuid4().hex[:12]
config = env['ir.config_parameter'].sudo()
for parameter, path in [('webhook_stock_update', '/stock'),
                        ('webhook_price_update', '/price'),
                        ('webhook_change_status', '/status')]:
    config.set_str(parameter, base + path)
config.set_str('webhook_api_key', 'isolated-webhook-test-key')
warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
warehouse.delivery_steps = 'pick_ship'
product = env['product.product'].create({
    'name': 'Disposable webhook goods', 'default_code': 'HELPER-HTTP-QA',
    'type': 'consu', 'is_storable': True, 'list_price': 100,
    'taxes_id': [Command.clear()],
})
partner = env['res.partner'].create({'name': 'Disposable webhook customer'})
env['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 2)
order = env['sale.order'].create({
    'partner_id': partner.id, 'warehouse_id': warehouse.id,
    'woocommerce_order_id': reference,
    'order_line': [Command.create({
        'product_id': product.id, 'product_uom_qty': 2,
        'price_unit': 100, 'tax_ids': [Command.clear()],
    })],
})
product.product_tmpl_id.list_price = 110
order.action_confirm()
result = order.complete_deliveries_for_paid_so()
assert len(result['validated']) == 2, result
assert not events, 'Notifications must wait for commit'
env.cr.commit()
with received:
    assert received.wait_for(lambda: {'/stock', '/price', '/status'} <= events.keys(), timeout=20), list(events)
assert events['/status']['woocommerce_order_id'] == reference
assert events['/status']['status'] == 'completed'
assert events['/stock']['products'][0]['on_hand'] == 0
assert 'HELPER-HTTP-QA' in events['/price']['skus']
for payload in events.values():
    assert payload['api_key'] == 'isolated-webhook-test-key'
# The response is recorded by the receiver just before the sender marks sent.
for attempt in range(40):
    # End REPEATABLE READ's snapshot before observing another thread's commit.
    env.cr.rollback()
    order.picking_ids.invalidate_recordset(['woocommerce_webhook_sent'])
    outgoing = order.picking_ids.filtered(lambda picking: picking.picking_type_code == 'outgoing')
    if outgoing.woocommerce_webhook_sent:
        break
    time.sleep(0.1)
assert outgoing.woocommerce_webhook_sent
server.shutdown()
print('PASS: real HTTP stock, price and completion webhooks after commit; success persisted')
