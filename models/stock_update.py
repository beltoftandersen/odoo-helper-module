"""Send stock after commit, coalescing within each transaction."""

import logging
import threading
import time

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)
STOCK_LOCATIONS_PARAM = 'webhook_stock_locations'
PENDING_KEY = 'odoowoo_stock_sync'


def _post_stock_webhook(url, payload):
    """A thread receives plain data only, never an ORM record or cursor."""
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=10,
                                     headers={'Content-Type': 'application/json'})
            if response.status_code in (200, 201, 202):
                return True
            _logger.warning('Stock webhook returned HTTP %s', response.status_code)
        except requests.RequestException:
            _logger.warning('Stock webhook request failed', exc_info=True)
        if attempt < 2:
            time.sleep(2 ** attempt)
    return False


def _dispatch_stock_webhook(url, payload):
    threading.Thread(target=_post_stock_webhook, args=(url, payload), daemon=True).start()


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.model
    def _get_webhook_location_ids(self):
        raw = self.env['ir.config_parameter'].sudo().get_str(STOCK_LOCATIONS_PARAM, '')
        ids = []
        for chunk in raw.split(','):
            try:
                if chunk.strip():
                    ids.append(int(chunk.strip()))
            except ValueError:
                _logger.warning('Ignoring invalid stock webhook location %r', chunk)
        locations = self.env['stock.location'].browse(ids).exists().filtered(
            lambda location: location.usage == 'internal'
        )
        if not locations:
            locations = self.env['stock.warehouse'].search([
                ('company_id', '=', self.env.company.id),
            ]).lot_stock_id
        return locations.ids

    @api.model
    def _get_stock_by_location(self, products, location_ids):
        result = {}
        for location_id in location_ids:
            scoped = products.with_context(location=location_id)
            result[str(location_id)] = {
                product.id: {
                    'on_hand': product.qty_available,
                    'forecast': product.virtual_available,
                    'available': product.qty_available - product.outgoing_qty,
                } for product in scoped
            }
        return result

    @api.model
    def _send_stock_webhook(self, products):
        config = self.env['ir.config_parameter'].sudo()
        url = config.get_str('webhook_stock_update', '')
        if not url or not products:
            return
        by_location = self._get_stock_by_location(products, self._get_webhook_location_ids())
        payload = {
            'timestamp': fields.Datetime.now().isoformat(),
            'api_key': config.get_str('webhook_api_key', ''),
            'odoo_db': self.env.cr.dbname,
            'odoo_url': config.get_str('web.base.url', ''),
            'operation': 'stock_update',
            'products': [{
                'product_id': product.id,
                'product_sku': product.default_code or '',
                'product_name': product.name,
                'on_hand': product.qty_available,
                'forecast': product.virtual_available,
                'available': product.qty_available - product.outgoing_qty,
                'by_location': {key: figures[product.id] for key, figures in by_location.items()},
            } for product in products],
        }
        _dispatch_stock_webhook(url, payload)

    @api.model
    def _schedule_stock_webhook(self, products):
        products = products.filtered(lambda product: product.is_storable and product.sale_ok)
        if not products or not self.env['ir.config_parameter'].sudo().get_str('webhook_stock_update', ''):
            return
        data = self.env.cr.postcommit.data
        pending = data.get(PENDING_KEY)
        if pending is None:
            pending = data[PENDING_KEY] = set()
            registry, uid, context = self.env.registry, self.env.uid, dict(self.env.context)

            def send_webhook():
                try:
                    with registry.cursor() as cursor:
                        env = api.Environment(cursor, uid, context)
                        env['stock.quant']._send_stock_webhook(
                            env['product.product'].browse(sorted(pending)).exists()
                        )
                except Exception:
                    _logger.exception('Stock webhook preparation failed after commit')

            self.env.cr.postcommit.add(send_webhook)
        pending.update(products.ids)


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, *args, **kwargs):
        result = super()._action_done(*args, **kwargs)
        self.env['stock.quant']._schedule_stock_webhook(self.product_id)
        return result

    def _action_assign(self, force_qty=False):
        result = super()._action_assign(force_qty=force_qty)
        if not self.env.context.get('skip_stock_webhook'):
            self.env['stock.quant']._schedule_stock_webhook(self.product_id)
        return result

    def _action_cancel(self):
        products = self.product_id
        result = super()._action_cancel()
        if not self.env.context.get('skip_stock_webhook'):
            self.env['stock.quant']._schedule_stock_webhook(products)
        return result

    def _trigger_assign(self):
        excluded = self.env.context.get('odoowoo_paid_receipt_ids', ())
        moves = self.filtered(lambda move: move.picking_id.id not in excluded)
        return super(StockMove, moves)._trigger_assign()


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        result = super(SaleOrder, self.with_context(skip_stock_webhook=True)).action_confirm()
        self.env['stock.quant']._schedule_stock_webhook(self.order_line.product_id)
        return result

    def action_cancel(self):
        products = self.order_line.product_id
        result = super(SaleOrder, self.with_context(skip_stock_webhook=True)).action_cancel()
        self.env['stock.quant']._schedule_stock_webhook(products)
        return result
