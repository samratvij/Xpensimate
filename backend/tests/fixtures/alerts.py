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
