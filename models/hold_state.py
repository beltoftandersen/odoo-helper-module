"""Retain Odoo's completion flow and reserve paid sales after PO receipts."""

from odoo import models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        receipts = self.filtered(
            lambda p: p.picking_type_id.code == 'incoming'
            and p.origin and p.origin.startswith('P')
        )
        # Suppress only broad receipt reservation, retaining core completion.
        result = super(StockPicking, self.with_context(
            odoowoo_paid_receipt_ids=receipts.ids,
        ))._action_done()
        if receipts:
            receipts.assign_deliveries_for_paid_so_self()
        return result

    def _send_confirmation_email(self):
        if not self.env.context.get('skip_delivery_email'):
            return super()._send_confirmation_email()

    def assign_deliveries_for_paid_so_self(self):
        products = self.move_line_ids.filtered(lambda line: line.quantity > 0).product_id
        if not products:
            return
        orders = self.env['sale.order'].search([
            ('order_line.product_id', 'in', products.ids),
            ('state', '=', 'sale'),
            ('invoice_status', '=', 'invoiced'),
        ])
        for order in orders:
            invoices = order.invoice_ids.filtered(
                lambda inv: inv.state == 'posted' and inv.move_type == 'out_invoice'
            )
            if invoices and all(inv.payment_state == 'paid' for inv in invoices):
                order.assign_deliveries_for_paid_so()
