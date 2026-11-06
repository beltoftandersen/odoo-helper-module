"""Complete fully reserved deliveries without forcing quantities."""

import logging

from odoo import models, _

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def complete_deliveries_for_paid_so(self):
        validated, skipped = [], []
        for order in self:
            processed = set()
            while True:
                # Odoo 20 can create the next step only when its preceding
                # transfer completes. Re-read instead of freezing the chain.
                picking = order.picking_ids.filtered(
                    lambda p: p.id not in processed and p.state not in ('done', 'cancel')
                ).sorted('id')[:1]
                if not picking:
                    break
                processed.add(picking.id)
                try:
                    # Keep failed validation atomic even when returning a result.
                    with self.env.cr.savepoint():
                        picking.action_assign()
                        moves = picking.move_ids.filtered(
                            lambda move: move.state not in ('done', 'cancel')
                        )
                        short = moves.filtered(
                            lambda move: move.product_id.is_storable
                            and move.uom_id.compare(move.quantity, move.product_uom_qty) < 0
                        )
                        if picking.state != 'assigned' or short:
                            skipped.append(picking.name)
                            continue
                        # Quantity already contains the reservation in Odoo 20.
                        # Mark existing lines picked; never invent stock or
                        # cancel outstanding demand to bypass a wizard.
                        moves.picked = True
                        picking.with_context(skip_delivery_email=True).button_validate()
                        if picking.state == 'done':
                            validated.append(picking.name)
                        else:
                            skipped.append(picking.name)
                except Exception:
                    _logger.exception('Could not complete delivery %s', picking.name)
                    skipped.append(picking.name)

        message = _('Validated: %s. Left open: %s.') % (
            ', '.join(validated) or _('none'),
            ', '.join(skipped) or _('none'),
        )
        return {
            'success': True,
            'log_message': message,
            'woocommerce_order_id': self[:1].woocommerce_order_id or False,
            'validated': validated,
            'skipped': skipped,
        }
