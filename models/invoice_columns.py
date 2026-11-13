# /models/invoice_columns.py
#
# Texts for the invoice report (views/report_invoice_columns.xml): the quantity with its unit of measure when that is not
# "Units", and the unit price with only the decimals it needs (2 to 4), in the invoice's language. A usage line then reads
# "312,00 Timer  0,0397" and a plan "1,00  35,00".

from odoo import models
from odoo.tools.misc import formatLang


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _odoowoo_env(self):
        return self.with_context(lang=self.move_id._get_lang()).env

    def _odoowoo_price_text(self):
        self.ensure_one()
        price = self.price_unit or 0.0
        decimals = 2
        for d in (2, 3, 4):
            decimals = d
            if abs(round(price, d) - price) < 1e-9:
                break
        return formatLang(self._odoowoo_env(), price, digits=decimals)

    def _odoowoo_quantity_text(self):
        self.ensure_one()
        env = self._odoowoo_env()
        text = formatLang(env, self.quantity or 0.0, digits=2)
        unit = env.ref('uom.product_uom_unit', raise_if_not_found=False)
        uom = self.product_uom_id
        if uom and (not unit or uom.id != unit.id):
            text = '%s %s' % (text, uom.with_env(env).name)
        return text
