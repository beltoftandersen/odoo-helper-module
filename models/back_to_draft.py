# /models/sale_order.py

from odoo import models, api, exceptions, _
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model
    def reset_order_by_id(self, sale_order_id):
        try:
            sale_order = self.browse(sale_order_id)
            if not sale_order.exists():
                msg = _("Sale Order with ID %s does not exist.") % sale_order_id
                raise exceptions.UserError(msg)

            if sale_order.state != 'cancel':
                msg = _("Sale Order %s is not in a canceled state and cannot be reset.") % sale_order_id
                raise exceptions.UserError(msg)

            sale_order.action_draft()
            log_message = _("Sale Order %s reset to draft successfully.") % sale_order_id
            _logger.info(log_message)

            woocommerce_order_id = sale_order.woocommerce_order_id or ""
            return {
                'success': True,
                'message': "Sale Order reset successfully.",
                'log_message': log_message,
                'woocommerce_order_id': woocommerce_order_id
            }

        except exceptions.UserError as ue:
            _logger.error(f"UserError resetting Sale Order {sale_order_id}: {ue}")
            return {
                'success': False,
                'message': str(ue),
                'log_message': str(ue),
                'woocommerce_order_id': ""
            }
        except Exception as e:
            _logger.exception(f"Unexpected error resetting Sale Order {sale_order_id}: {e}")
            return {
                'success': False,
                'message': str(e),
                'log_message': str(e),
                'woocommerce_order_id': ""
            }


    def set_to_invoice_status(self):
        with self.env.cr.savepoint():
            self.write({'invoice_status': 'to invoice'})
            self.message_post(
                body=_("Invoice status has been set to 'To Invoice'."),
                subtype_xmlid='mail.mt_note',
            )
        return True

    def reset_all_deliveries_to_waiting(self):
        # Picking state is computed from stock moves. Unreserve through core
        # instead of writing a state that disagrees with the actual quants.
        pickings = self.picking_ids.filtered(
            lambda picking: picking.state not in ('done', 'cancel')
        )
        pickings.do_unreserve()
        self.env['stock.quant']._schedule_stock_webhook(pickings.move_ids.product_id)
        return True
