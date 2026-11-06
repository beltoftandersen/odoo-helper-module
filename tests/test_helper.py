from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.odoowoo_helper.models import price_webhook, stock_move, stock_update


@tagged('post_install', '-at_install')
class TestOdooWooHelper(AccountTestInvoicingCommon):
    country_code = 'US'
    chart_template = 'generic_coa'

    @classmethod
    def get_default_groups(cls):
        return super().get_default_groups() | cls.env.ref('sales_team.group_sale_manager')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'Helper customer'})
        cls.goods = cls._create_product(
            name='Helper goods', default_code='HELPER-GOODS', type='consu',
            is_storable=True, invoice_policy='order', list_price=100,
            taxes_id=[Command.clear()],
        )
        cls.warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.env.company.id),
        ], limit=1)
        cls.bank = cls.company_data['default_journal_bank']

    def setUp(self):
        super().setUp()
        self.env.cr.postcommit.clear()
        for key in ('webhook_api_key', 'webhook_stock_update', 'webhook_price_update',
                    'webhook_change_status', 'webhook_stock_locations'):
            self.env['ir.config_parameter'].sudo().set_str(key, '')

    def tearDown(self):
        self.env.cr.postcommit.clear()
        super().tearDown()

    def _order(self, quantity=1, reference='QA_123', uom=None):
        return self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'warehouse_id': self.warehouse.id,
            'woocommerce_order_id': reference,
            'order_line': [Command.create({
                'product_id': self.goods.id,
                'product_uom_qty': quantity,
                'product_uom_id': (uom or self.goods.uom_id).id,
                'price_unit': 100,
                'tax_ids': [Command.clear()],
            })],
        })

    def _invoice(self, move_type='out_invoice', amount=100):
        invoice = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': self.customer.id,
            'invoice_date': fields.Date.today(),
            'woocommerce_order_id': 'QA_123',
            'invoice_line_ids': [Command.create({
                'product_id': self.goods.id, 'quantity': 1,
                'price_unit': amount, 'tax_ids': [Command.clear()],
            })],
        })
        invoice.action_post()
        return invoice

    def _add_stock(self, quantity, location=None):
        self.env['stock.quant']._update_available_quantity(
            self.goods, location or self.warehouse.lot_stock_id, quantity,
        )

    def _configure_webhooks(self):
        config = self.env['ir.config_parameter'].sudo()
        for key in ('webhook_stock_update', 'webhook_price_update', 'webhook_change_status'):
            config.set_str(key, 'https://example.invalid/helper-test')
        config.set_str('webhook_api_key', 'test-key')

    def test_unique_store_order(self):
        self._order()
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self._order()

    def test_batch_picking_creation_preserves_references(self):
        orders = self._order(reference='QA_1') | self._order(reference='QA_2')
        values = [{
            'origin': order.name,
            'picking_type_id': self.warehouse.out_type_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.customer.property_stock_customer.id,
            'move_ids': [Command.create({
                'product_id': self.goods.id,
                'product_uom_qty': 1, 'uom_id': self.goods.uom_id.id,
                'location_id': self.warehouse.lot_stock_id.id,
                'location_dest_id': self.customer.property_stock_customer.id,
            })],
        } for order in orders]
        pickings = self.env['stock.picking'].create(values)
        self.assertEqual(pickings.mapped('woocommerce_order_id'), ['QA_1', 'QA_2'])
        self.assertEqual(pickings.move_ids.mapped('woocommerce_order_id'), ['QA_1', 'QA_2'])

    def test_confirm_and_invoice_with_dates(self):
        order = self._order()
        result = order.confirm_order_by_id(order.id, '2026-10-20 10:00:00')
        self.assertTrue(result['success'])
        self.assertEqual(str(order.date_order), '2026-10-20 10:00:00')
        result = order.create_invoice_by_order_id(order.id, '2026-10-21')
        self.assertTrue(result['success'])
        invoice = self.env['account.move'].browse(result['invoice_ids'])
        self.assertEqual(invoice.woocommerce_order_id, order.woocommerce_order_id)
        self.assertEqual(str(invoice.invoice_date), '2026-10-21')
        self.assertEqual(invoice.state, 'posted')

    def test_payment_partial_then_remaining(self):
        invoice = self._invoice()
        for amount, reference in [(40, 'first'), (60, 'second')]:
            result = self.env['payment.register'].register_payment(
                invoice.id, self.bank.id, reference, '2026-10-22', amount,
            )
            self.assertTrue(result['success'], result)
        self.assertAlmostEqual(invoice.amount_residual, 0)
        payments = self.env['account.payment'].search([('custom_payment_ref', 'in', ['first', 'second'])])
        self.assertEqual(len(payments), 2)
        self.assertEqual(set(payments.mapped('custom_payment_ref')), {'first', 'second'})

    def test_overpayment_rejected_without_payment(self):
        invoice = self._invoice()
        count = self.env['account.payment'].search_count([])
        result = self.env['payment.register'].register_payment(invoice.id, self.bank.id, amount=150)
        self.assertFalse(result['success'])
        self.assertEqual(self.env['account.payment'].search_count([]), count)
        self.assertEqual(invoice.amount_residual, 100)

    def test_rounding_difference_settles_only_residual(self):
        invoice = self._invoice()
        result = self.env['payment.register'].register_payment(invoice.id, self.bank.id, amount=100.01)
        self.assertTrue(result['success'], result)
        self.assertAlmostEqual(invoice.amount_residual, 0)

    def test_refund_payment(self):
        invoice = self._invoice(move_type='out_refund')
        result = self.env['payment.register'].register_payment(invoice.id, self.bank.id, 'refund')
        self.assertTrue(result['success'], result)
        self.assertAlmostEqual(invoice.amount_residual, 0)
        payment = self.env['account.payment'].search([('custom_payment_ref', '=', 'refund')])
        self.assertEqual(payment.payment_type, 'outbound')

    def test_payment_failure_rolls_back_created_payment(self):
        invoice = self._invoice()
        model = type(self.env['account.payment.register'])
        original = model._create_payments
        count = self.env['account.payment'].search_count([])

        def fail_after_create(wizard):
            original(wizard)
            raise ValueError('Simulated failure after payment creation')

        with patch.object(model, '_create_payments', fail_after_create):
            result = self.env['payment.register'].register_payment(invoice.id, self.bank.id)
        self.assertFalse(result['success'])
        self.assertEqual(self.env['account.payment'].search_count([]), count)
        self.assertEqual(invoice.amount_residual, 100)

    def test_non_finite_and_negative_payments_rejected(self):
        invoice = self._invoice()
        for amount in (-1, 0, float('nan'), float('inf')):
            result = self.env['payment.register'].register_payment(invoice.id, self.bank.id, amount=amount)
            self.assertFalse(result['success'])
        self.assertEqual(invoice.amount_residual, 100)

    def test_reserved_delivery_completes_without_negative_stock(self):
        self._add_stock(10)
        order = self._order(quantity=10)
        order.action_confirm()
        result = order.complete_deliveries_for_paid_so()
        self.assertEqual(result['validated'], order.picking_ids.mapped('name'))
        self.assertEqual(order.picking_ids.state, 'done')
        self.assertEqual(self.goods.qty_available, 0)
        self.assertEqual(order.order_line.qty_delivered, 10)

    def test_partial_reservation_remains_open(self):
        self._add_stock(6)
        order = self._order(quantity=10)
        order.action_confirm()
        result = order.complete_deliveries_for_paid_so()
        self.assertFalse(result['validated'])
        self.assertEqual(result['skipped'], order.picking_ids.mapped('name'))
        self.assertNotEqual(order.picking_ids.state, 'done')
        self.assertEqual(order.picking_ids.move_ids.product_uom_qty, 10)
        self.assertEqual(self.goods.qty_available, 6)
        self.assertFalse(order.picking_ids.backorder_ids)

    def test_delivery_in_converted_unit(self):
        self._add_stock(24)
        order = self._order(quantity=2, uom=self.uom_dozen)
        order.action_confirm()
        result = order.complete_deliveries_for_paid_so()
        self.assertTrue(result['validated'])
        self.assertEqual(self.goods.qty_available, 0)
        self.assertEqual(order.order_line.qty_delivered, 2)

    def test_two_step_delivery_completes_in_order(self):
        self.warehouse.delivery_steps = 'pick_ship'
        self._add_stock(10)
        order = self._order(quantity=10)
        order.action_confirm()
        # Odoo 20 initially creates PICK and pushes OUT on its completion.
        self.assertEqual(len(order.picking_ids), 1)
        result = order.complete_deliveries_for_paid_so()
        self.assertEqual(len(result['validated']), 2)
        self.assertTrue(all(p.state == 'done' for p in order.picking_ids))
        self.assertEqual(self.goods.qty_available, 0)

    def test_live_custom_quantity_and_location_context(self):
        self._add_stock(10)
        self.assertEqual(self.goods.custom_available_quantity, 10)
        order = self._order(quantity=3)
        order.action_confirm()
        self.assertEqual(self.goods.custom_available_quantity, 7)
        self.assertEqual(self.goods.product_tmpl_id.custom_available_quantity, 7)
        other = self.env['stock.location'].create({'name': 'Separate stock', 'usage': 'internal'})
        self._add_stock(4, other)
        self.assertEqual(self.goods.with_context(location=other.id).custom_available_quantity, 4)

    def test_stock_notification_includes_goods_and_merges_events(self):
        self._configure_webhooks()
        order = self._order()
        order.action_confirm()
        self.assertEqual(self.env.cr.postcommit.data[stock_update.PENDING_KEY], {self.goods.id})
        order.assign_deliveries_for_paid_so()
        self.assertEqual(self.env.cr.postcommit.data[stock_update.PENDING_KEY], {self.goods.id})

    def test_stock_payload_locations_and_warehouse_fallback(self):
        self._add_stock(10)
        self._configure_webhooks()
        quant = self.env['stock.quant']
        self.assertIn(self.warehouse.lot_stock_id.id, quant._get_webhook_location_ids())
        self.env['ir.config_parameter'].sudo().set_str(
            'webhook_stock_locations', f'bad,{self.warehouse.lot_stock_id.id},99999999',
        )
        with patch.object(stock_update, '_dispatch_stock_webhook') as dispatch:
            quant._send_stock_webhook(self.goods)
        payload = dispatch.call_args.args[1]
        self.assertEqual(payload['products'][0]['on_hand'], 10)
        self.assertEqual(payload['products'][0]['by_location'][str(self.warehouse.lot_stock_id.id)]['available'], 10)

    def test_unchanged_price_does_not_notify(self):
        self._configure_webhooks()
        self.goods.product_tmpl_id.list_price = self.goods.list_price
        self.assertNotIn(price_webhook.PENDING_KEY, self.env.cr.postcommit.data)

    def test_base_price_changes_merge_skus(self):
        self._configure_webhooks()
        self.goods.product_tmpl_id.list_price = 110
        self.goods.product_tmpl_id.list_price = 120
        entry = self.env.cr.postcommit.data[price_webhook.PENDING_KEY]['entries'][None]
        self.assertEqual(entry['skus'], {self.goods.default_code})

    def test_pricelist_lookup_fixed_discount_and_markup(self):
        pricelist = self.env['product.pricelist'].create({'name': 'Helper prices'})
        for values, expected in [({'compute_price': 'fixed', 'fixed_price': 42}, 42),
                                 ({'compute_price': 'discount', 'price_discount': 10}, 90),
                                 ({'compute_price': 'markup', 'base': 'list_price', 'price_markup': 20}, 120)]:
            pricelist.item_ids.unlink()
            self.env['product.pricelist.item'].create(dict(values,
                pricelist_id=pricelist.id, applied_on='1_product', product_tmpl_id=self.goods.product_tmpl_id.id,
            ))
            result = pricelist.get_prices_for_skus(pricelist.id, [self.goods.default_code])
            self.assertEqual(result[0]['price'], expected)

    def test_pricelist_change_notifies(self):
        pricelist = self.env['product.pricelist'].create({'name': 'Helper prices'})
        self._configure_webhooks()
        item = self.env['product.pricelist.item'].create({
            'pricelist_id': pricelist.id, 'applied_on': '1_product',
            'product_tmpl_id': self.goods.product_tmpl_id.id,
            'compute_price': 'fixed', 'fixed_price': 50,
        })
        self.assertIn(pricelist.id, self.env.cr.postcommit.data[price_webhook.PENDING_KEY]['entries'])
        self.env.cr.postcommit.clear()
        item.fixed_price = 60
        self.assertEqual(self.env.cr.postcommit.data[price_webhook.PENDING_KEY]['entries'][pricelist.id]['skus'], {self.goods.default_code})

    def test_completion_status_waits_for_all_deliveries(self):
        self._configure_webhooks()
        self._add_stock(10)
        order = self._order(quantity=2)
        order.action_confirm()
        self.assertNotIn(stock_move.PENDING_KEY, self.env.cr.postcommit.data)
        result = order.complete_deliveries_for_paid_so()
        self.assertTrue(result['validated'])
        self.assertEqual(self.env.cr.postcommit.data[stock_move.PENDING_KEY], {order.woocommerce_order_id})
        self.assertFalse(order.picking_ids.woocommerce_webhook_sent)

    def test_no_status_configuration_does_not_mark_sent(self):
        self._add_stock(2)
        order = self._order(quantity=2)
        order.action_confirm()
        order.complete_deliveries_for_paid_so()
        self.assertFalse(order.picking_ids.woocommerce_webhook_sent)
        self.assertNotIn(stock_move.PENDING_KEY, self.env.cr.postcommit.data)

    def test_legacy_webhook_configuration_rpc(self):
        config = self.env['ir.config_parameter'].sudo()
        config.set_param('webhook_api_key', 'new-test-key')
        self.assertEqual(config.get_str('webhook_api_key'), 'new-test-key')

    def test_receipt_keeps_unpaid_order_unreserved(self):
        order = self._order(quantity=5)
        order.action_confirm()
        receipt = self.env['stock.picking'].create({
            'origin': 'P-HELPER-TEST',
            'picking_type_id': self.warehouse.in_type_id.id,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
            'move_ids': [Command.create({
                'product_id': self.goods.id, 'product_uom_qty': 10,
                'uom_id': self.goods.uom_id.id,
                'location_id': self.env.ref('stock.stock_location_suppliers').id,
                'location_dest_id': self.warehouse.lot_stock_id.id,
            })],
        })
        receipt.action_confirm()
        receipt.move_ids.quantity = 10
        receipt.move_ids.picked = True
        receipt.with_context(skip_delivery_email=True).button_validate()
        self.assertEqual(receipt.state, 'done')
        self.assertEqual(self.goods.qty_available, 10)
        self.assertEqual(order.picking_ids.move_ids.quantity, 0)

    def test_status_failure_does_not_mark_sent(self):
        self._configure_webhooks()
        self._add_stock(2)
        order = self._order(quantity=2)
        order.action_confirm()
        order.complete_deliveries_for_paid_so()
        with patch.object(stock_move.requests, 'post', side_effect=RuntimeError('Offline')):
            stock_move._post_status_webhook(
                self.env.registry, self.env.uid, dict(self.env.context),
                order.picking_ids.ids, 'https://example.invalid', {},
            )
        self.assertFalse(order.picking_ids.woocommerce_webhook_sent)

    def test_cancel_and_reset(self):
        order = self._order()
        order.action_confirm()
        result = order.cancel_woocommerce_sales_order(order.woocommerce_order_id)
        self.assertTrue(result['success'], result)
        self.assertEqual(order.state, 'cancel')
        result = order.reset_order_by_id(order.id)
        self.assertTrue(result['success'], result)
        self.assertEqual(order.state, 'draft')

    def test_reset_delivery_releases_real_reservations(self):
        self._add_stock(10)
        order = self._order(quantity=5)
        order.action_confirm()
        self.assertEqual(order.picking_ids.move_ids.quantity, 5)
        self.assertTrue(order.reset_all_deliveries_to_waiting())
        self.assertEqual(order.picking_ids.move_ids.quantity, 0)
        self.assertEqual(self.goods.free_qty, 10)
        self.assertEqual(self.goods.qty_available, 10)

    def test_invoice_status_api_preserved(self):
        order = self._order()
        self.assertTrue(order.set_to_invoice_status())
        self.assertEqual(order.invoice_status, 'to invoice')

    def test_completion_status_ignores_cancelled_transfers(self):
        self._configure_webhooks()
        self._add_stock(2)
        order = self._order(quantity=2)
        order.action_confirm()
        cancelled = order.picking_ids.copy({'origin': order.name})
        cancelled.action_cancel()
        self.assertEqual(cancelled.woocommerce_order_id, order.woocommerce_order_id)
        order.complete_deliveries_for_paid_so()
        self.assertEqual(self.env.cr.postcommit.data[stock_move.PENDING_KEY], {order.woocommerce_order_id})
