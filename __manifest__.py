# -*- coding: utf-8 -*-
{
    'name': "OdooWoo Helper",
    'summary': """
        This module creates necessary fields, enables webhooks, etc. for Chimkins Woocommerce integration.""",

    'description': """
        This module creates necessary fields as Woocommerce order ID, enables webhooks for sending stock updates and order statuses to Woocommerce.
    """,

    'author': "Chimkins IT",
    'website': "https://chimkins.com",
    'category': 'Technical',
    'version': '20.0.1.1.0',

    'depends': ['sale_stock', 'account'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'views/fields.xml',
        'views/payment_ref.xml',
        'views/search.xml',
        'views/report_invoice_columns.xml',
    ],

    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
