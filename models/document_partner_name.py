# /models/document_partner_name.py
#
# The name printed above an address on invoices and other documents.
#
# OdooWoo keeps each invoice and delivery address as a child contact of the
# customer, so a posted invoice keeps the address it was issued with. Odoo
# prints a child as "Parent, Child", which for a private customer reads
# "Christian Beltoft-Andersen, Christian Beltoft-Andersen". On a document
# (context show_address, which the contact widget of every report sets) the
# address contact speaks for itself:
#
# - the same person as the customer: only the address contact's own name;
# - a company (any customer with another name): the company first, the contact under it.
#
# Everywhere else (lists, search, the contact form) Odoo's own name is kept.

from odoo import models

ADDRESS_TYPES = ('invoice', 'delivery')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _get_complete_name(self):
        self.ensure_one()

        if not (self.env.context.get('show_address') and self.parent_id and self.type in ADDRESS_TYPES and not self.is_company):
            return super()._get_complete_name()

        own = (self.name or '').strip()
        commercial = self.sudo().commercial_partner_id
        owner = (self.commercial_company_name or commercial.name or '').strip()

        # The customer is the same person as the address contact: one name. A company (or an older
        # company contact stored as a person) differs from it: the company first, the contact under it.
        if not own or own.lower() == owner.lower():
            return own or owner

        return f"{owner}\n{own}" if owner else own
