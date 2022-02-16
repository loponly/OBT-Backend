import falcon
import time
from .base import Route, StandardResponse, auth_guard
from routes.utility.acl import ACLManager
from routes.utility.users import UserManager
from .utility.payment.payment_utility import *
from .utility.ob_token import get_price_in_usd
from .policy import HoldingTiers, SubscriptionFeePerTrade, SubscriptionPolicy

from .spectree import spectree
from spectree import Response
from pydantic import BaseModel
from typing import List, Optional
from result import Result, Ok, Err


class CustomersPostResp(BaseModel):
    customer_id: str


class SubscriptionGetResp(BaseModel):
    next_billing_date: Optional[int]
    current_amount_due: Optional[float]
    current_amount_due_eur: Optional[float]
    status: Optional[str]
    cancel_at_period_end: Optional[bool]
    current_period_start: Optional[int]
    product_name: Optional[str]
    last_profit_calculated: Optional[float]
    last_profit_time: Optional[int]


class SubscriptionPostResp(BaseModel):
    subscription: str


class SubscriptionDeleteResp(BaseModel):
    success: bool = True


class PaymentMethodGetModel(BaseModel):
    method: str
    last4: Optional[str]
    country: Optional[str]
    bank_code: Optional[str]
    brand: Optional[str]
    exp_month: Optional[int]
    exp_year: Optional[int]


class PaymentMethodGetResp(BaseModel):
    __root__: PaymentMethodGetModel


class StripeCustomers(Route):
    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=CustomersPostResp))
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        customer_id = PaymentUtils(self.dbs).add_customer(username)
        resp.media = {'customer_id': customer_id}


def _set_obt_payments(profile, use_obt: bool) -> Result[None, str]:
    if (profile['payment'].get('payment_type', 'Fiat') == 'OBT') == use_obt:
        # Active payment type is already correct
        return Ok(None)

    if not profile.get('obt_token'):
        return Err("OBT Wallet is not active, please enable before trying to use it as payment")

    subscription_id = profile['payment'].get('subscription_id')
    if not subscription_id:
        return Err("Switching to/from OBT can only be done with active subscriptions")

    if not use_obt:
        try:
            stripe.Subscription.delete_discount(subscription_id)
        except stripe.error.InvalidRequestError:
            pass

    if use_obt:
        promo_ids = stripe.PromotionCode.list(code='USEOBT', limit=1)['data']
        promo = promo_ids[0]['id']
        stripe.Subscription.modify(subscription_id, promotion_code=promo)

    profile['payment']['payment_type'] = 'Fiat' if not use_obt else 'OBT'
    return Ok(None)


class StripeSubscription(Route):
    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=SubscriptionGetResp))
    def on_get(self, req, resp):
        profile = self.get_profile(req).unwrap()

        if profile['payment'].get('policy_id') == SubscriptionFeePerTrade.sub:

            resp.media = {
                'status': 'active',
                'product_name': SubscriptionFeePerTrade.sub
            }
            return
        

        if not profile['payment'].get('subscription_id'):
            resp.media = {'debug': 'Customer has no subscription with payments'}
            resp.status = falcon.HTTP_400
            return

        subscr_id = profile['payment']['subscription_id']
        stripe_resp_subscr = stripe.Subscription.retrieve(subscr_id)
        stripe_resp_price = stripe.Price.retrieve(profile['payment'].get('price_ids', [None])[0] or price_id)
        product_id = stripe_resp_price['product']
        stripe_resp_product = stripe.Product.retrieve(product_id)
        product_name = stripe_resp_product['name']

        last_profit_calculated = profile['payment'].get('last_profit_calculated', None)

        resp.media = {
            'next_billing_date': stripe_resp_subscr['current_period_end'],
            'current_amount_due': profile['payment']['billing_amount'],
            'current_amount_due_eur': profile['payment'].get('billing_amount_eur', None),
            'status': stripe_resp_subscr['status'],
            'cancel_at_period_end': stripe_resp_subscr['cancel_at_period_end'],
            'current_period_start': stripe_resp_subscr['current_period_start'],
            'last_profit_calculated': last_profit_calculated,
            'last_profit_time': profile['payment'].get('last_profit_time', None),
            'product_name': product_name
        }

    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=SubscriptionPostResp, HTTP_400=StandardResponse))
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.get_profile(req).unwrap()
        sub = req.media['subscription']
        promo = req.media.get('promo_code')
        use_obt = req.media.get('use_obt')


        if profile['payment'].get('subscription_id'):
            resp.media = {'error': 'Customer already has a subscription'}
            resp.status = falcon.HTTP_400
            return

        customer_id = profile['payment']['customer_id']

        aclm = ACLManager(self.dbs)
        acl = aclm.get_acl(SubscriptionPolicy._key)
        for entry in acl:
            if entry.sub == sub:
                total_active_bots = len(list(filter(lambda bot:bot if bot['enabled'] else False,[self.dbs['bots'][b] for b in profile['bots']])))
                if total_active_bots > entry.allowed_bots:
                    resp.status = falcon.HTTP_400
                    resp.media = {'error': f'Deactivate at least {total_active_bots-entry.allowed_bots} bots to enable subsciption.'}
                    return
                items = list(map(lambda p: {'price': p}, entry.price_ids))
                if not items:
                    resp.status = falcon.HTTP_500
                    resp.media = {'error': 'Subscription could not be processed'}
                    return
                break
        else:
            resp.status = falcon.HTTP_400
            resp.media = {'error': 'Subscription not found'}
            return

        if promo:
            promo_ids = stripe.PromotionCode.list(code=promo, limit=1)['data']
            if len(promo_ids) < 1:
                resp.status = falcon.HTTP_400
                resp.media = {'error': 'Promo code is not valid'}
                return

            coupon = stripe.Coupon.retrieve(promo_ids[0]['coupon']['id'], expand=["applies_to"])
            promo = promo_ids[0]['id']

            is_appliable = False
            for item in items:
                price_obj = stripe.Price.retrieve(item['price'])
                if price_obj['product'] in coupon['applies_to'].get('products', []):
                    is_appliable = True
                    break

            if not is_appliable:
                resp.status = falcon.HTTP_400
                resp.media = {'error': 'Promo code is not applicable to this plan'}
                return

        if use_obt:
            nominal_value = get_items_value(items)
            if not profile.get('obt_token'):
                resp.status = falcon.HTTP_400
                resp.media = {'error': 'OBT Wallet is not active, please enable before trying to use it as payment'}
                return
            obt_price = get_price_in_usd(self.dbs)
            new_bal = profile['obt_token'].get('balance', 0) - ((nominal_value / obt_price) * int(1e18))
            if new_bal <= 0:
                resp.status = falcon.HTTP_400
                resp.media = {'error': f'Not enough OBT in wallet to pay for subscription (missing {round(abs(new_bal / int(1e18)), 2) + 0.01} OBT)'}
                return

            promo_ids = stripe.PromotionCode.list(code='USEOBT', limit=1)['data']
            promo = promo_ids[0]['id']

        # create subscription for the customer using the price id (reference to the business model we created)
        # items = [{'price': price_id}]
        subscription = stripe.Subscription.create(customer=customer_id, items=items, expand=['latest_invoice.payment_intent'], promotion_code=promo or None)
        profile['payment']['subscription_id'] = subscription['id']
        profile['payment']['price_ids'] = list(map(lambda d: d['price'], items))
        profile['payment']['policy_id'] = sub
        profile['payment']['payment_type'] = 'Fiat' if not use_obt else 'OBT'

        # will be used as reference for adding amount due
        profile['payment']['subscr_item_id'] = subscription['items']['data'][0]['id']

        # Reset billing profit so we only count profits made after starting the subscription
        for botid in profile['bots']:
            bot = self.dbs['bots'][botid]
            bot['billing_start_portfolio'] = bot['state'].portfolioValue
            self.dbs['bots'][botid] = bot

        self.dbs['users'][username] = profile

        resp.media = {'subscription': subscription['id']}

    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=SubscriptionDeleteResp))
    def on_delete(self, req, resp):
        profile = self.get_profile(req).unwrap()
        subscription_id = profile['payment'].get('subscription_id')
        if not subscription_id:
            resp.media = {'error': 'No subscription active'}
            resp.status = falcon.HTTP_400
            return
            
        stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)

        resp.media = {'success': True}

    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=SubscriptionDeleteResp))
    def on_put(self, req, resp):
        profile = self.get_profile(req).unwrap()
        subscription_id = profile['payment'].get('subscription_id')
        if not subscription_id:
            resp.media = {'error': 'No paid subscription active'}
            resp.status = falcon.HTTP_400
            return

        data = req.media or {}
        if data.get('use_obt') is not None:
            res = _set_obt_payments(profile, data.get('use_obt'))
            if res.is_err():
                resp.status = falcon.HTTP_400
                resp.media = {'error': res.err()}
                return

            self.update_profile(req, profile)
        stripe.Subscription.modify(subscription_id, cancel_at_period_end=False)
        resp.media = {'success': True}


class StripeSetupIntent(Route):
    @auth_guard
    def on_get(self, req, resp):
        profile = self.get_profile(req).unwrap()
        assert profile['payment'].get('customer_id', None) != None, "No customer ID found"
        pm_type = req.params.get('payment_method_type', 'card')
        intent = stripe.SetupIntent.create(customer=profile['payment']['customer_id'], payment_method_types=[pm_type])
        resp.media = intent.client_secret


class StripePaymentMethods(Route):
    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=PaymentMethodGetResp))
    def on_get(self, req, resp):
        profile = self.get_profile(req).unwrap()

        # No details on OBT payments
        if profile['payment'].get('payment_type') == 'OBT':
            resp.media = {'method': 'OBT'}
            return

        pm_id = profile['payment'].get('payment_method_id', None)
        if not pm_id:
            resp.media = {'debug': 'No payment method attached yet'}
            resp.status = falcon.HTTP_400
            return

        stripe_resp = stripe.PaymentMethod.retrieve(pm_id)
        if stripe_resp['type'] not in ['card', 'sepa_debit']:
            resp.media = {'error': 'Used payment method is not fully supported, please contact the developers'}
            resp.status = falcon.HTTP_400
            return

        if stripe_resp['type'] == 'card':
            result = {
                'brand': stripe_resp['card']['brand'],
                'last4': stripe_resp['card']['last4'],
                'exp_month': stripe_resp['card']['exp_month'],
                'exp_year': stripe_resp['card']['exp_year'],
                'country': stripe_resp['card']['country'],
                'method': stripe_resp['type']
            }
        elif stripe_resp['type'] == 'sepa_debit':
            result = {
                'bank_code': stripe_resp['sepa_debit']['bank_code'],
                'country': stripe_resp['sepa_debit']['country'],
                'last4': stripe_resp['sepa_debit']['last4'],
                'method': stripe_resp['type']
            }

        resp.media = result

    @auth_guard
    def on_post(self, req, resp):
        profile = self.get_profile(req).unwrap()
        username = self.get_username(req).unwrap()

        customer_id = profile['payment']['customer_id']

        # set payment method to default
        pmlist = []
        pmlist.extend(stripe.PaymentMethod.list(customer=customer_id, type='card'))
        pmlist.extend(stripe.PaymentMethod.list(customer=customer_id, type='sepa_debit'))
        pmlist = sorted(pmlist, key=lambda d: d['created'])
        latest = pmlist[-1]

        stripe.Customer.modify(customer_id, invoice_settings={'default_payment_method': latest['id']})

        profile['payment']['payment_method_id'] = latest['id']

        # Disable OBT payments when adding a stripe payment method
        _set_obt_payments(profile, False)

        profile['payment']['enabled'] = True
        self.dbs['users'][username] = profile

    @auth_guard
    def on_delete(self, req, resp):
        profile = self.get_profile(req).unwrap()
        customer_id = profile['payment']['customer_id']

        if profile['payment'].get('subscription_id'):
            resp.media = {'error': 'Subscription still active'}
            resp.status = falcon.HTTP_400
            return

        customer = stripe.Customer.retrieve(customer_id)
        pmid = customer['invoice_settings']['default_payment_method']
        if pmid is None:
            resp.status = falcon.HTTP_400
            resp.media = {'error': 'No payment method attached'}
            return

        stripe.PaymentMethod.retrieve(pmid).detach()
        stripe.Customer.modify(customer_id, invoice_settings={'default_payment_method': None})

        del profile['payment']['payment_method_id']
        self.update_profile(req, profile).unwrap()

        resp.media = {'success': True}


class UserSubscription(Route):
    @auth_guard
    def on_get(self, req, resp):
        profile = self.get_profile(req).unwrap()
        aclm = ACLManager(self.dbs)
        policy = aclm.find_policy(SubscriptionPolicy._key, profile).get()
        policy = aclm.override_policy(profile,policy)

        resp.media = {'current': policy}


class AvailableSubcriptions(Route):
    @auth_guard
    def on_get(self, req, resp):
        profile = self.get_profile(req).unwrap()
        aclm = ACLManager(self.dbs)
        acl = aclm.get_acl(SubscriptionPolicy._key)

        relevant_tier_free_tier = aclm.find_policy(SubscriptionPolicy._key, profile, lambda pol: pol.payment_fees == 0 and pol.price_per_month == 0 and pol.pct_per_trade == 0).get()


        subscriptions = list(filter(lambda entry: entry['billingType'] != 'Free' and not entry.get('is_hidden'), map(lambda entry: entry.get(), acl)))

        for entry in subscriptions:
            items = list(map(lambda p: {'price': p}, entry['price_ids']))
            # TODO: cache get_items_value?
            entry = aclm.override_policy(profile,entry)
            cost_per_month = entry.get('price_per_month', 0)

            nominal_value = cost_per_month if cost_per_month > 0 else get_items_value(items)
            obt_price = get_price_in_usd(self.dbs)
            entry['obt_price'] = nominal_value / obt_price

        current_tier = aclm.find_policy(SubscriptionPolicy._key, profile).get()
        if current_tier.get('is_hidden'):
            subscriptions.append(current_tier)
        subscriptions.append(relevant_tier_free_tier)
        resp.media = subscriptions


class CurrentHoldingTier(Route):
    @auth_guard
    def on_get(self, req, resp):
        profile = self.get_profile(req).unwrap()
        aclm = ACLManager(self.dbs)
  
        resp.media = aclm.get_current_holding_tier(HoldingTiers._key,profile).get()

class AllHoldingTier(Route):
    
    @auth_guard
    def on_get(self, req, resp):
        acl = self.dbs['globals'][HoldingTiers._key]
        acl = list(map(lambda entry: entry.dict(exclude={'_key'}), acl))
        resp.media = acl

def fuzzya_eq(a, b, t=1e-5):
    return ((a + t) > b) and ((b + t) > a)


class BotPaymentDetails(Route):
    @auth_guard
    def on_get(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        policy = UserManager(self.dbs).get_policy(username)
        data = {}
        for botid in profile['bots']:
            bot = self.dbs['bots'][botid]

            portfolios = self.dbs['bot_portfolios'].get(botid, None) or {int(time.time()): bot['state'].portfolioValue}
            current = portfolios[list(portfolios.keys())[-1]]

            start = bot.get('billing_start_portfolio', current)
            if fuzzya_eq(start, current, t=0.005) and not bot['enabled']:
                continue
            data[botid] = {}
            d = data[botid]
            d['current_portfolio'] = current
            d['start_portfolio'] = start
            d['fees_calculated'] = (current - start) * policy.payment_fees
            d['fees_percentage'] = policy.payment_fees
        resp.media = data


class UserInvoices(Route):
    @auth_guard
    def on_get(self, req, resp):
        profile = self.get_profile(req).unwrap()

        customer_id = profile['payment']['customer_id']
        if customer_id == None:
            resp.media = []
            return
        invoices = stripe.Invoice.list(customer=customer_id)
        data = [
            {
                'total': invoice['total'],
                'amount_due': invoice['amount_due'],
                'tax': invoice['tax'],
                'paid': invoice['paid'],
                'attempted': invoice['attempted'],
                'created': invoice['created'],
                'period_start': invoice['period_start'],
                'period_end': invoice['period_end'],
                'paid_time': invoice['status_transitions']['paid_at'],
                'url': invoice['hosted_invoice_url'],
                'pdf': invoice['invoice_pdf'],
            }
            for invoice in invoices
        ]

        resp.media = data


class FeePerTrade(Route):

    @auth_guard
    def on_get(self, req, resp):
        profile = self.get_profile(req).unwrap()
        
        if profile['payment'].get('policy_id') == SubscriptionFeePerTrade.sub:
            resp.media = {
                'status': 'active',
                'product_name': SubscriptionFeePerTrade.sub
            }
            return
        
        resp.media = {'error': 'No subscription active'}
        resp.status = falcon.HTTP_400
        return 


    @auth_guard
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.get_profile(req).unwrap()
        use_obt = req.media.get('use_obt')


        if profile['payment'].get('subscription_id'):
            resp.media = {'error': 'Customer already has a subscription'}
            resp.status = falcon.HTTP_400
            return

        
        aclm = ACLManager(self.dbs)

        policy = aclm.get_policy(SubscriptionPolicy._key,SubscriptionFeePerTrade.sub)
        if not policy:
            resp.status = falcon.HTTP_400
            resp.media = {'error': 'Subscription not found'}
            return

        res = SubscriptionFeePerTrade(self.dbs).upgrade(username,use_obt,policy)
        if res.is_err():
            resp.status = falcon.HTTP_500
            resp.media = {'error': res.err()}
            return
        resp.media = {'subscription': res.ok()}
        return
     


    
    @auth_guard
    def on_delete(self, req, resp):
        profile = self.get_profile(req).unwrap()
        username = self.get_username(req).unwrap()
        
        if profile['payment'].get('policy_id') != SubscriptionFeePerTrade.sub:
            resp.media = {'error': 'No subscription active'}
            resp.status = falcon.HTTP_400
            return 

        aclm = ACLManager(self.dbs)
        policy = aclm.get_policy(SubscriptionPolicy._key,'Free')
        if not policy:
            resp.status = falcon.HTTP_400
            resp.media = {'error': 'Subscription not found'}
            return
        
        
        res = SubscriptionFeePerTrade(self.dbs).downgrade(username,True,policy)
        if res.is_err():
            resp.status = falcon.HTTP_500
            resp.media = {'error': res.err()}
            return
            
        for b in profile['bots']:
            bot= self.dbs['bots'][b]
            bot['enabled'] = False
            self.dbs['bots'][b]=bot
        resp.media = {'subscription': res.ok()}
        

    @auth_guard
    def on_put(self, req, resp):
        profile = self.get_profile(req).unwrap()
        username = self.get_username(req).unwrap()

            
        from_sub =profile['payment'].get('policy_id')
        if from_sub == SubscriptionFeePerTrade.sub:
            resp.media = {'error': 'No subscription active'}
            resp.status = falcon.HTTP_400
            return 

        aclm = ACLManager(self.dbs)
        to_policy = aclm.get_policy(SubscriptionPolicy._key, SubscriptionFeePerTrade.sub)
        from_policy = aclm.get_policy(SubscriptionPolicy._key,from_sub)
        if not to_policy or not from_policy:
            resp.status = falcon.HTTP_400
            resp.media = {'error': 'Subscription not found'}
            return
        
        _holding_tier = aclm.get_current_holding_tier(HoldingTiers._key,profile)

        total_active_bots = len(list(filter(lambda bot:bot if bot['enabled'] else False,[self.dbs['bots'][b] for b in profile['bots']])))
        if total_active_bots > _holding_tier.allowed_bots:
            resp.status = falcon.HTTP_400
            resp.media = {'error': f'Deactivate at least {total_active_bots-_holding_tier.allowed_bots} bots to enable subsciption.'}
            return
        

        subscription_id = profile['payment'].get('subscription_id')
        res = SubscriptionFeePerTrade(self.dbs).upgrade(username,True,to_policy)
        if res.is_err():
            resp.status = falcon.HTTP_500
            resp.media = {'error': res.err()}
            return
        
        try:
            stripe.Subscription.delete(subscription_id,invoice_now=True)
        except Exception as e:
            if 'No such subscription' in str(e):
                pass
            raise e

        resp.media = {'subscription': res.ok()}

        

      
