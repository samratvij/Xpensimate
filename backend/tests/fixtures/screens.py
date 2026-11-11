"""What the AI model reads from bank-app screenshots (the rows read_screenshot returns), modeled on real screenshots with
the balances left out."""

# RBC app, Sam's Chequing (4150), Oct 2026: pay, e-transfers to Alex, a transfer, an ATM withdrawal.
RBC_SAM_CHEQUING = [
    {"date": "2026-10-29", "merchant": "Payroll Deposit PAIE/PAYROLL", "amount": 2980.20, "kind": "deposit", "pending": False},
    {"date": "2026-10-29", "merchant": "e-Transfer sent jordan wu KQ3MZ1", "amount": 494.00, "kind": "purchase", "pending": False},
    {"date": "2026-10-29", "merchant": "e-Transfer sent alex lee WB7TQP", "amount": 1000.00, "kind": "purchase", "pending": False},
    {"date": "2026-10-29", "merchant": "Online Banking transfer - 2046", "amount": 1650.40, "kind": "purchase", "pending": False},
    {"date": "2026-10-27", "merchant": "ATM withdrawal - ATM00421", "amount": 200.00, "kind": "purchase", "pending": False},
]

# A deposit that isn't pay.
E_TRANSFER_RECEIVED = {"date": "2026-10-30", "merchant": "e-Transfer received Jordan", "amount": 60.00, "kind": "deposit",
                       "pending": False}

# Balance readings (what read_balances returns), modeled on real account screenshots (Nov 2026).
# PC Financial app home: Mastercard, PC Money and its Savings.
PC_BALANCES = [
    {"shown_name": "PC® Mastercard® •••• 9775", "balance": 240.10, "owed": True, "currency": "CAD", "account_id": "pc_mastercard"},
    {"shown_name": "PC Money™ Account •••• 4812", "balance": 31.64, "owed": False, "currency": "CAD", "account_id": "pc_money"},
    {"shown_name": "Savings", "balance": 37812.40, "owed": False, "currency": "CAD", "account_id": "pc_savings"},
]
# Wealthsimple "Spend & Save" (Alex's phone), including a US-dollar account the household doesn't track.
WS_BALANCES = [
    {"shown_name": "Credit card Visa Infinite Privilege", "balance": 1320.15, "owed": True, "currency": "CAD", "account_id": "ws_credit"},
    {"shown_name": "Checking", "balance": 410.25, "owed": False, "currency": "CAD", "account_id": "ws_cash"},
    {"shown_name": "Savings", "balance": 5.21, "owed": False, "currency": "CAD", "account_id": "ws_savings"},
    {"shown_name": "Family", "balance": 0.03, "owed": False, "currency": "CAD", "account_id": "ws_joint"},
    {"shown_name": "US savings", "balance": 0.0, "owed": False, "currency": "USD", "account_id": "unknown"},
]
# One account's page: Alex's TFSA.
TFSA_BALANCE = [{"shown_name": "TFSA", "balance": 5450.30, "owed": False, "currency": "CAD", "account_id": "ws_tfsa"}]
