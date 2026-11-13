# /models/invoice_line_label.py
#
# The text printed for a product line on an invoice.
#
# Odoo prints a product line as the product's full name ("[SKU] Product (attributes)") followed by the
# line's description. OdooWoo already writes the description the customer saw in the shop, so with the
# system parameter odoowoo_helper.label_is_description set to 1 a product line with a description prints
# only that description. Off (the default), Odoo's own text is kept.

from odoo import models

PARAM = 'odoowoo_helper.label_is_description'


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _compute_label(self):
        super()._compute_label()

        if self.env['ir.config_parameter'].sudo().get_param(PARAM, '') not in ('1', 'true', 'True'):
            return

        for line in self:
            if line.product_id and line.name:
                line.label = line.name
