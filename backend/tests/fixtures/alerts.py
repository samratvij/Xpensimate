"""Bank alert texts. Tangerine and PC Financial are real alerts (Nov 2026) with the name and card
numbers redacted; CTFS is still synthetic until a real NotifyMe purchase alert is seen."""

TANGERINE_CC_SUBJECT = "A new Credit Card transaction has been made"
TANGERINE_CC_BODY = """Tangerine
Hi Alex,
Just a quick note to let you know that a transaction of $11.29 was made on your Tangerine ® Money-Back World Mastercard ®* 5000 xxxx xxxx 1234 at DOORDASHDASHPASS on November 01, 2026.
If you have any questions, please don't hesitate to call us at 1-888-826-4374.
Sincerely,
Tangerine
Forward Banking"""

PC_MASTERCARD_SUBJECT = "PC® Mastercard® purchase notice"
PC_MASTERCARD_BODY = """Hi Alex,
A purchase of $32.70 was made on your PC ® Mastercard ®  card ending in 5678 on November 02, 2026.
Card number: ************5678
Merchant: MAPLE DENTAL CENTRE
Purchase amount: $32.70
Transaction date: November 02, 2026
View your transactions and manage your alerts by logging in to your online account .
If you or an additional cardholder on the account did not make this purchase, please call us at 1 877 939 3933.
You're receiving this email because you are a President’s Choice Bank cardholder and provided your email address to us. This is not a promotional email. At any time, you may unsubscribe  from promotional email sent by President’s Choice Bank."""

PC_MONEY_SUBJECT = "PC Money™ Account purchase notice"
PC_MONEY_BODY = """Hi Alex,
A purchase of $420.00 was made on your PC Money™ Account card ending in 9012 on November 01, 2026.
Card number: ************9012
Merchant:
Purchase amount: $420.00
Transaction date: November 01, 2026"""

PC_LOW_BALANCE_SUBJECT = "Your PC Money™ Account balance is below  $100.00"
PC_LOW_BALANCE_BODY = """Hi Alex,
Your PC Money™ Account balance is below $100.00. You can add money at any time.
Card ending in 9012"""

CTFS_OTP_SUBJECT = "Your One Time Code"
CTFS_OTP_BODY = "Your one time code is 00000000\nYou are receiving a one-time code because you're trying to access your profile."

# Synthetic.
CTFS_PURCHASE_SUBJECT = "NotifyMe: Purchase made"
CTFS_PURCHASE_BODY = """Here are the details of your recent purchase.
Purchase Amount: $1,234.56
Merchant: CANADIAN TIRE #123
Card ending in: 3456"""

# Real RBC alert (Nov 2026; card number redacted). The plain-text part keeps HTML entities.
RBC_PURCHASE_SUBJECT = "You made a purchase."
RBC_PURCHASE_BODY = """&#847; &zwnj; &#8199; &#847; &zwnj; &#8199;
https://click.alerts.rbc.com/?qs=EXAMPLE
Hello,
As requested, we&rsquo;re letting you know that a purchase of $22.59 was made on your RBC Royal Bank credit card account ************4321 on November 04, 2026 towards CANADIAN TIRE STORE #2.
If you don&rsquo;t recognize this transaction, please call us at 1&#8209;800&#8209;769&#8209;2512 (available 24/7) and we&rsquo;ll be happy to help.
Account:
************4321
Purchase Amount:
$22.59
Transaction Date:
November 04, 2026
Transaction Description:
CANADIAN TIRE STORE #2
Thank you!
https://view.alerts.rbc.com/?qs=EXAMPLE
View in a browser"""

# Synthetic variants of the same template.
RBC_SENTENCE_ONLY_BODY = """Hello,
As requested, we&rsquo;re letting you know that a purchase of $1,076.40 was made on your RBC Royal Bank credit card account ************4321 on November 04, 2026 towards FARM BOY #12.
If you don&rsquo;t recognize this transaction, please call us."""

RBC_REFUND_SUBJECT = "You received a refund."
RBC_REFUND_BODY = """Hello,
As requested, we&rsquo;re letting you know that a refund of $19.99 was made on your RBC Royal Bank credit card account ************4321 on November 05, 2026 towards UNIQLO CANADA.
Account:
************4321
Refund Amount:
$19.99
Transaction Description:
UNIQLO CANADA"""

RBC_DEBIT_SUBJECT = "You made a purchase."
RBC_DEBIT_BODY = "As requested, a purchase of $12.00 was made with your RBC Client Card (debit card) ************9999 towards METRO 123."

# Wealthsimple "Direct deposit received" email, modeled on a real one (Oct 2026), plain-text part with the links and referral
# block trimmed. The payer is Alex's employer.
WS_DIRECT_DEPOSIT_SUBJECT = "Direct deposit received"
WS_DIRECT_DEPOSIT_BODY = """[Wealthsimple]
---------------------------------------------------------------
We got your direct deposit!
Amount: $3,250.40
From: NORTHWIND
Your deposit is now available in your chequing account.
Give $25, get $25
---------------------------------------------------------------
Have questions? We're here to help, visit our Help Centre"""

# Modeled on a real email (Oct 2026): a move between the household's own Wealthsimple accounts.
WS_TRANSFER_SUBJECT = "You made a transfer"
WS_TRANSFER_BODY = """You made a transfer
Amount: $450.00 CAD
From: Checking (Chequing)
To: \N{HOUSE WITH GARDEN}Family (Joint Chequing)
Track the status of this transfer in the activity feed."""

# Synthetic until a real RBC deposit alert arrives: Sam's pay into RBC Chequing (account ending 4150), as
# the RBC app lists it ("Payroll Deposit PAIE/PAYROLL", Oct 29 2026).
RBC_DEPOSIT_SUBJECT = "A deposit was made to your account."
RBC_DEPOSIT_BODY = """Hello,
As requested, we&rsquo;re letting you know that a deposit of $2,980.20 was made to your RBC Royal Bank account ************4150 on October 29, 2026.
Account:
************4150
Deposit Amount:
$2,980.20
Transaction Description:
Payroll Deposit PAIE/PAYROLL"""

RBC_DEPOSIT_NO_PAYER_BODY = """Hello,
As requested, we&rsquo;re letting you know that a deposit of $2,980.20 was made to your RBC Royal Bank account ************4150 on October 29, 2026."""

RBC_SMALL_DEPOSIT_BODY = """Hello,
A deposit of $40.00 was made to your RBC Royal Bank account ************4150 on October 30, 2026.
Transaction Description:
Mobile cheque deposit"""

# Modeled on a real email (Oct 2026): Sam moving money into the joint account (their own transfers name them).
WS_SAM_TRANSFER_SUBJECT = "Sam made a transfer"
WS_SAM_TRANSFER_BODY = """Sam made a transfer
Amount: $200.00 CAD
From: Chequing (******000CAD)
To: \N{HOUSE WITH GARDEN}Family (Joint Chequing)
Initiated by: Sam Park
Track the status of this transfer in the activity feed."""

# Modeled on a real email (Oct 2026): out of the joint account back to Alex's chequing.
WS_FROM_JOINT_BODY = """You made a transfer
Amount: $650.00 CAD
From: \N{HOUSE WITH GARDEN}Family (Joint Chequing)
To: Checking (Chequing)
Track the status of this transfer in the activity feed."""

# Synthetic: a TFSA contribution, in the same template.
WS_TFSA_TRANSFER_BODY = """You made a transfer
Amount: $500.00 CAD
From: Checking (Chequing)
To: TFSA (TFSA)
Track the status of this transfer in the activity feed."""
