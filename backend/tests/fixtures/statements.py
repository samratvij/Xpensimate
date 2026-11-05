"""Statement exports in each bank's real layout (headers, signs, date formats), with made-up rows."""

CTM = """MY ACCOUNT TRANSACTIONS
Start Date,End Date,Current Balance,Available Credit
2026-10-24,2026-10-26,1944.72,6055.28
REF,TRANSACTION DATE,POSTED DATE,TYPE,DESCRIPTION,Category,AMOUNT
#10000000000000000000001,2026-10-24,2026-10-26,PURCHASE,BOWLERAMA NORTH,Entertainment,65.88
#10000000000000000000002,2026-10-26,2026-10-28,PURCHASE,SHELL C04456,Gas,85.9
"""

PCF = '''"Description","Type","Card Holder Name","Date","Time","Amount"
"CINEPLEX CINECLUB","PURCHASE","ALEX","10/10/2026","04:35 AM","-12.42"
"MAPLE DENTAL CENTRE","PURCHASE","ALEX","10/02/2026","03:43 PM","-32.70"
"Pymt Money Account 9012","PAYMENT","ALEX","10/01/2026","09:00 AM","420.00"
"SHOPPERS DRUG MART #06","REFUND","ALEX","10/05/2026","12:00 PM","13.87"
"PURCHASE INTEREST CHARGE","INTEREST","ALEX","10/20/2026","12:00 AM","-12.60"
"LOBLAWS 1000","PURCHASE","ALEX","09/28/2026","06:39 PM","-104.23"
'''

TANGERINE = """Transaction date,Transaction,Name,Memo,Amount
10/23/2026,DEBIT,TIM HORTONS #1000 TORONTO ON,Rewards earned: 0.24 ~ Category: Restaurant,-11.82
10/22/2026,DEBIT,DRIVETEST CENTRE 12 TORONTO ON,Rewards earned: 0.80 ~ Category: Other,-159.75
10/15/2026,CREDIT,PAYMENT - THANK YOU,,764.36
"""

WEALTHSIMPLE = """transaction_date,transaction_type,status,merchant,amount,currency,notes,category
2026-10-27,Purchase,Completed,Corner Creamery Toronto,-9.29,CAD,,Other food and drink
2026-10-26,Purchase,Completed,Rogers ******0000,-79.10,CAD,,Internet and phone
2026-10-25,Payment,Completed,,374.88,CAD,,Uncategorized
2026-10-24,Purchase,Pending,Somewhere Pending,-5.00,CAD,,Other
2026-10-20,Refund,Completed,Sports Outlet Midtown,423.73,CAD,,Shopping
"""
