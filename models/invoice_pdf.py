# /models/invoice_pdf.py

import base64
import logging
import re

from odoo import models, api, exceptions, _

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def render_invoice_pdf_by_id(self, move_id):
        """The customer-facing PDF of a posted customer invoice, for the middleware.

        XML-RPC cannot call the private report renderer, so this public method
        calls it on the middleware's behalf. Only posted out_invoice records
        are rendered. Returns success, filename and content_b64.
        """
        move = self.browse(move_id)
        if not move.exists():
            raise exceptions.UserError(_("No invoice found with ID %s.") % move_id)
        if move.move_type != 'out_invoice' or move.state != 'posted':
            return {'success': False, 'message': _("Only posted customer invoices can be rendered.")}
        try:
            pdf, _fmt = self.env['ir.actions.report']._render_qweb_pdf('account.account_invoices', [move.id])
        except Exception as e:  # report engine errors vary by version
            _logger.error("Rendering invoice %s failed: %s", move.id, e)
            return {'success': False, 'message': str(e)}
        base = re.sub(r'[^A-Za-z0-9._-]+', '_', move.name or str(move.id)).strip('_') or str(move.id)
        return {
            'success': True,
            'filename': f"{base}.pdf",
            'content_b64': base64.b64encode(pdf).decode('ascii'),
        }
