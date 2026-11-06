"""Keep the existing OdooWoo webhook setup RPC working on Odoo 20."""

from odoo import api, models


class IrConfigParameter(models.Model):
    _inherit = 'ir.config_parameter'

    @api.model
    def set_param(self, key, value):
        # OdooWoo still calls this legacy name to send webhook strings.
        # Retain native access checks; never sudo the caller's write.
        return self.set_str(key, value)
