import stripe
import os
from routes.db import get_exchange_rates

if os.environ.get('ENVIRONMENT', "dev") == 'prod':
    api_key = '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'
    price_id = 'price_1IbOVSCmzAW8QZBCPbCfck3f'
else:
    api_key = 'sk_test_51I6v1YCmzAW8QZBC04udclw1K1HinpL5hAWQbjFihprA6xIjXdG2WweDvOSM8NxSTNMaQ0sG9yTQZ4qeRNo6AFOv0063EImrZq'
    price_id = 'price_1IbOTbCmzAW8QZBC7g2ur9qX'

stripe.api_key = api_key


def get_items_value(items):
    exchange_rates = get_exchange_rates()
    v = 0
    for p in items:
        price = stripe.Price.retrieve(p['price'])
        unit_price = float(price['unit_amount'])
        if price.get('currency', 'usd') != 'usd':
           unit_price *= exchange_rates[price['currency'].upper()]
        else:
            v += unit_price 

    return v / 100

class PaymentUtils():
    def __init__(self, dbs: dict):
        self.dbs = dbs

    def add_customer(self, email):
        profile = self.dbs['users'].get(email, None)
        assert profile, "User not found"

        # backwards compatibility
        if not profile.get('payment', None):
            profile['payment'] = {}
            self.dbs['users'][email] = profile
        
        if profile['payment'].get('customer_id', None):
            try:
                customer = stripe.Customer.retrieve(profile['payment']['customer_id'])
                return profile['payment']['customer_id']
            except SystemExit:
                return
            except Exception:
                pass

        name = profile['name']
        customer = stripe.Customer.create(email=email, name=name)

        profile['payment']['customer_id'] = customer['id']
        self.dbs['users'][email] = profile

        return customer['id']
