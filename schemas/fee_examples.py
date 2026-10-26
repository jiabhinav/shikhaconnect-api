"""Swagger request examples; example months follow an April–March session."""

MONTH_VALUES = {
    f'{year}-{month:02}': {'amount': '500.00', 'enabled': month != 6}
    for year, months in ((2026, range(4, 13)), (2027, range(1, 4)))
    for month in months
}

FEE_CONFIGURATION_EXAMPLES = {
    'month_wise': {
        'summary': 'Edit all 12 months (June unchecked)',
        'description': 'Use months matching the selected session. Unchecked months still submit an amount; responses display null.',
        'value': {
            'fee_structure_id': 1, 'fee_category_id': 1, 'no_of_fee_heads': 1,
            'fee_heads': [{
                'fee_head_id': 1, 'amount': '500.00', 'edit_months_wise': True,
                'month_amounts': MONTH_VALUES,
            }],
        },
    },
    'same_amount': {
        'summary': 'Same fee for every session month',
        'description': 'The response automatically includes every session month at the base amount.',
        'value': {
            'fee_structure_id': 1, 'fee_category_id': 1, 'no_of_fee_heads': 1,
            'fee_heads': [{
                'fee_head_id': 1, 'amount': '500.00', 'edit_months_wise': False,
                'month_amounts': {},
            }],
        },
    },
}

FEE_MONTH_PATCH_EXAMPLES = {
    'edit_selected_months': {
        'summary': 'Change April and uncheck June',
        'value': {'month_amounts': {
            '2026-04': {'amount': '450.00', 'enabled': True},
            '2026-06': {'amount': '500.00', 'enabled': False},
        }},
    },
    'base_amount': {
        'summary': 'Update the base amount for months without overrides',
        'value': {'amount': '600.00'},
    },
}
