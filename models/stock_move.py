"""Copy store references and notify completion only after stock commits."""

import logging
import threading

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)
PENDING_KEY = 'odoowoo_status_sync'


def _post_status_webhook(registry, uid, context, picking_ids, url, payload):
    try:
        response = requests.post(url, json=payload, timeout=10,
                                 headers={'Content-Type': 'application/json'})
        response.raise_for_status()
        with registry.cursor() as cursor:
            env = api.Environment(cursor, uid, context)
            env['stock.picking'].browse(picking_ids).exists().write({
                'woocommerce_webhook_sent': True,
            })
            cursor.commit()
    except Exception:
        _logger.exception('Order completion webhook failed')


def _dispatch_status_webhook(*args):
    threading.Thread(target=_post_status_webhook, args=args, daemon=True).start()


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    woocommerce_order_id = fields.Char(string='WooCommerce Order ID', copy=False)
    woocommerce_webhook_sent = fields.Boolean(string='WooCommerce Webhook Sent', copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        origins = {vals['origin'] for vals in vals_list if vals.get('origin')}
        orders = self.env['sale.order'].search([('name', 'in', list(origins))])
        references = {order.name: order.woocommerce_order_id for order in orders}
        for vals in vals_list:
            if not vals.get('woocommerce_order_id') and references.get(vals.get('origin')):
                vals['woocommerce_order_id'] = references[vals['origin']]
        pickings = super().create(vals_list)
        for picking in pickings:
            if picking.woocommerce_order_id:
                picking.move_ids.woocommerce_order_id = picking.woocommerce_order_id
        return pickings

    def button_validate(self):
        result = super().button_validate()
        for picking in self.filtered(
            lambda p: p.state == 'done' and p.woocommerce_order_id
            and p.picking_type_id.code == 'outgoing'
        ):
            picking._check_and_send_webhook()
        return result

    def _check_and_send_webhook(self):
        self.ensure_one()
        if self.woocommerce_webhook_sent:
            return
        related = self.search([
            ('woocommerce_order_id', '=', self.woocommerce_order_id),
            ('picking_type_id.code', '=', 'outgoing'),
            ('state', '!=', 'cancel'),
        ])
        if related and all(picking.state == 'done' for picking in related):
            self._send_woocommerce_webhook(related)

    def _send_woocommerce_webhook(self, related=None):
        self.ensure_one()
        config = self.env['ir.config_parameter'].sudo()
        url = config.get_str('webhook_change_status', '')
        key = config.get_str('webhook_api_key', '')
        if not url or not key:
            return
        pending = self.env.cr.postcommit.data.setdefault(PENDING_KEY, set())
        if self.woocommerce_order_id in pending:
            return
        pending.add(self.woocommerce_order_id)
        args = (
            self.env.registry, self.env.uid, dict(self.env.context),
            (related or self).ids, url,
            {'woocommerce_order_id': self.woocommerce_order_id,
             'status': 'completed',
             'date_done': self.date_done.isoformat() if self.date_done else None,
             'api_key': key},
        )
        self.env.cr.postcommit.add(lambda: _dispatch_status_webhook(*args))
