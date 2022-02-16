import stripe
import falcon
import time
import uuid

from routes.utility.notifications import NotificationHandler
from .base import Route
from routes.utility.acl import ACLManager
from .policy import SubscriptionPolicy
from .logging import getLogger

from datetime import date, datetime
from dateutil.relativedelta import relativedelta

from .utility.payment.payment_utility import api_key, get_items_value
from .utility.ob_token import get_price_in_usd, notify
from .utils import safe_del

stripe.api_key = api_key

# for stripe use only
# undocumented from spectree for that reason


class StripeHooks(Route):
    # events are handled and proper functions are called based on them
    def on_post(self, req, resp):
        l = getLogger('payment.stripe.hooks')
        event = None

        try:
            event = stripe.Event.construct_from(req.media, stripe.api_key)
        except ValueError as e:
            l.warn(f"Failed to parse stripe event: {e}")
            resp.status = falcon.HTTP_400
            return

        # TODO: validate signing secret

        if event.type == 'invoice.payment_succeeded':
            email = event.data.object['customer_email']
            self.payment_success(email, event.data.object)
        elif event.type in ['customer.subscription.deleted', 'customer.subscription.canceled']:
            subscription_id = event.data.object['id']
            self.subscription_deleted(subscription_id)
        elif event.type == 'invoice.payment_failed':
            email = event.data.object['customer_email']
            self.payment_failed(email)
        elif event.type == 'invoice.upcoming':
            pass
        else:
            #l.warn(f"No handler for {event.type}")
            resp.status = falcon.HTTP_204
            return

        resp.status = falcon.HTTP_202

    def payment_success(self, email, obj={}):
        l = getLogger('payment.stripe.hooks.payment_success')
        profile = self.dbs['users'][email]

        billing_datetime = datetime.fromtimestamp(time.time())
        profile['payment']['next_billing_date'] = datetime.timestamp(billing_datetime + relativedelta(months=+1))

        if (obj.get('discount', {}) or {}).get('coupon', {}).get('id') == 'USEOBT':
            aclm = ACLManager(self.dbs)
            acl = aclm.get_acl(SubscriptionPolicy._key)
            cost_per_month = 0
            for entry in acl:
                if entry.sub == profile['payment']['policy_id']:
                    items = list(map(lambda p: {'price': p}, entry.price_ids))
                    cost_per_month = entry.get().get('price_per_month', 0)
                    break
            else:
                l.warn('Subscription for {email} could not be processed: no policy found for {profile["payment"]["policy_id"]}')
                return

            nominal_value = cost_per_month if cost_per_month > 0 else get_items_value(items)
            obt_price = get_price_in_usd(self.dbs)
            obt_cost = nominal_value / obt_price
            new_bal = profile['obt_token'].get('balance', 0) - (obt_cost * int(1e18))
            if new_bal < 0:
                # In-app notification for unsuccessful OBT charge
                notify_data = {
                    'title': f"Unsuccessful OBT payment attempt for your {profile['payment']['policy_id']} plan",
                    'body': f"Unsuccessful charge attempt was initiated for {round(obt_cost, 4)} OBT (${nominal_value}) from your OB wallet. Your {profile['payment']['policy_id']} plan with billing period of {billing_datetime:%d/%m/%Y %H:%M} UTC - {(billing_datetime + relativedelta(months=1)):%d/%m/%Y %H:%M} UTC was automatically discontinued"
                }
                NotificationHandler(self.dbs).add_subscription_plan_notification(email, notify_data)

                # Email for unsuccessful OBT charge
                notify(username=email,
                       data={
                           'template_id': 'd-fb4182a8a24d4054a24121a8b08ec332',
                           'profile': profile,
                           'body': {
                               'date': f'{billing_datetime:%d/%m/%Y %H:%M}',
                               'billing_period_start': f'{billing_datetime:%d/%m/%Y %H:%M}',
                               'billing_period_end': f'{(billing_datetime + relativedelta(months=1)):%d/%m/%Y %H:%M}',
                               'amount_obt': round(obt_cost, 4),
                               'amount_usd': nominal_value,
                               'plan': profile['payment']['policy_id']
                           }
                       })
                stripe.Subscription.delete(profile['payment']['subscription_id'])
                profile = remove_subscription(self.dbs, profile)
                self.dbs['users'][email] = profile
                return

            txs = self.dbs['token_transactions'].get(email, [])
            txs.append({'date': time.time(), 'type': 'SUBSCRIPTION', 'amount': 0, 'fee': obt_cost, 'price': obt_price})
            profile['obt_token']['balance'] = new_bal
            # Pre-emptive save in case there is an issue afterwards
            # TODO: context manager?
            self.dbs['users'][email] = profile
            self.dbs['token_transactions'][email] = txs

            # In-app notification for successful OBT charge
            notify_data = {
                'title': f"Successful {profile['payment']['policy_id']} plan payment ({billing_datetime:%d/%m/%Y %H:%M} UTC - {(billing_datetime + relativedelta(months=1)):%d/%m/%Y %H:%M} UTC)",
                'body': f"Your OBT wallet was successfully charged {round(obt_cost, 4)} OBT (${nominal_value}) for your {profile['payment']['policy_id']} plan for the billing period between {billing_datetime:%d/%m/%Y %H:%M} UTC and {(billing_datetime + relativedelta(months=1)):%d/%m/%Y %H:%M} UTC"
            }
            NotificationHandler(self.dbs).add_subscription_plan_notification(email, notify_data)

            # Email for successful OBT charge
            notify(username=email,
                   data={
                       'template_id': 'd-41d54062953b4466ac74d7ec20b1e814',
                       'profile': profile,
                       'body': {
                           'date': f'{datetime.now():%d/%m/%Y %H:%M}',
                           'billing_period_start': f'{billing_datetime:%d/%m/%Y %H:%M}',
                           'billing_period_end': f'{(billing_datetime + relativedelta(months=1)):%d/%m/%Y %H:%M}',
                           'amount_obt': round(obt_cost, 4),
                           'amount_usd': nominal_value,
                           'plan': profile['payment']['policy_id']
                       }
                   })

        for bots in profile['bots']:
            with self.dbs['bots'].transact(retry=True):
                bot = self.dbs['bots'][bots]
                bot['billing_start_portfolio'] = bot['state'].portfolioValue
                self.dbs['bots'][bots] = bot

        self.dbs['users'][email] = profile

    def payment_failed(self, email):
        profile = self.dbs['users'][email]
        # TODO: only disable bots, allow re-enable when payment_success within a week?
        profile = remove_subscription(self.dbs, profile)
        self.dbs['users'][email] = profile

    def subscription_deleted(self, subscription_id):
        profile = None
        user = None
        for username in self.dbs['users']:
            if self.dbs['users'][username].get('payment', {}).get('subscription_id', '') == subscription_id:
                profile = self.dbs['users'][username]
                user = username
                break

        profile = remove_subscription(self.dbs, profile)
        # TODO: note canceled instead of free?
        self.dbs['users'][user] = profile


def remove_subscription(dbs, profile):
    for b in profile['bots']:
        bot = dbs['bots'][b]
        if bot['enabled']:
            bot['enabled'] = False
            bot['stop_time'] = time.time()
            dbs['bots'][b] = bot

    safe_del(profile['payment'], 'subscription_id')
    safe_del(profile['payment'], 'subscr_item_id')
    safe_del(profile['payment'], 'price_ids')
    safe_del(profile['payment'], 'payment_type')

    profile['payment']['policy_id'] = 'Free'
    # TODO: notification
    profile['payment']['enabled'] = False
    return profile


def create_usage_record(profile, dbs, profits):
    # if the user has no subscription, do nothing
    subscription = stripe.Subscription.retrieve(profile['payment']['subscription_id'])
    if subscription['pause_collection']:
        return

    subscription_item_id = None
    for si in subscription['items']['data']:
        if si['price']['billing_scheme'] == 'per_unit':
            subscription_item_id = si['id']
            break
    else:
        raise NotImplementedError("Usage record not implemented for non-per_unit items")

    policy = ACLManager(dbs).find_policy(SubscriptionPolicy._key, profile)

    amount_due = profits * policy.payment_fees * 100  # in cents
    amount_due = max(amount_due, 0)

    # Used to avoid retry issues (not applicable)
    idempotency_key = uuid.uuid4()

    stripe.SubscriptionItem.create_usage_record(
        subscription_item_id, quantity=int(amount_due), timestamp=int(time.time()),
        action='set', idempotency_key=str(idempotency_key))
