"""Swagger request examples; example months follow an April–March session."""

SESSION_MONTHS = (
    [(2026, month) for month in range(4, 13)]
    + [(2027, month) for month in range(1, 4)]
)


def month_array(amount, disabled_months=()):
    return [
        {
            'month_key': f'{year:04}-{month:02}',
            'amount': amount,
            'enabled': month not in disabled_months,
            'is_override': True,
        }
        for year, month in SESSION_MONTHS
    ]

FEE_CONFIGURATION_EXAMPLES = {
    'month_wise': {
        'summary': 'Edit all 12 months (June unchecked)',
        'description': 'Use months matching the selected session. Unchecked months still submit an amount; responses display null.',
        'value': {
            'fee_structure_id': 1, 'fee_category_id': 1, 'no_of_fee_heads': 1,
            'fee_heads': [{
                'fee_head_id': 1, 'base_amount': '500.00', 'edit_months_wise': True,
                'months': month_array('500.00', disabled_months=(6,)),
            }],
        },
    },
    'same_amount': {
        'summary': 'Same fee for every session month',
        'description': 'The response automatically includes every session month at the base amount.',
        'value': {
            'fee_structure_id': 1, 'fee_category_id': 1, 'no_of_fee_heads': 1,
            'fee_heads': [{
                'fee_head_id': 1, 'base_amount': '500.00', 'edit_months_wise': True,
                'months': month_array('500.00'),
            }],
        },
    },
}

FEE_CONFIGURATION_UPDATE_EXAMPLES = {
    'month_array': {
        'summary': 'Update with month_key array format',
        'description': 'configuration_id, school_id, and session_id identify the existing configuration; only fee_heads is needed in the request body.',
        'value': {
            'fee_heads': [
                {
                    'fee_head_id': 3,
                    'base_amount': '3500.00',
                    'edit_months_wise': True,
                    'months': [
                        {'month_key': f'{year:04}-{month:02}', 'amount': '3500.00',
                         'enabled': month == 4, 'is_override': True}
                        for year, month in SESSION_MONTHS
                    ],
                },
                {
                    'fee_head_id': 4,
                    'base_amount': '2500.00',
                    'edit_months_wise': True,
                    'months': [
                        {'month_key': f'{year:04}-{month:02}', 'amount': '2500.00',
                         'enabled': True, 'is_override': True}
                        for year, month in SESSION_MONTHS
                    ],
                },
                {
                    'fee_head_id': 6,
                    'base_amount': '500.00',
                    'edit_months_wise': True,
                    'months': [
                        {'month_key': f'{year:04}-{month:02}', 'amount': '500.00',
                         'enabled': month in (7, 2), 'is_override': True}
                        for year, month in SESSION_MONTHS
                    ],
                },
            ],
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
