import logging
from routes.exchange import Exchange
from routes.utility.notify_alert import NotifyMailPreferences
from routes.disk import DillCache
from sys import platform

from routes.utility.ob_token import OBTokenTransaction

from .logging import getLogger
from .db import get_dbs
from .utility.notifications import NotificationHandler
from .utility.notif_saving import Notification, NotificationSaving
from tradeEnv.utils import span_from_candletype
from tradeEnv.metrics import MarketInfo
from tradeEnv.realtime import RealTimeEnv
import numpy as np
import datetime
import pickle
import traceback
import os
import sys
import time
import atexit
from setproctitle import setproctitle, getproctitle
from multiprocessing import Process, Pool
from functools import partial

from .utility.strategy import StrategyFactory
from .base import add_pkg
from routes import db

add_pkg()


def get_pkl_path(exchange, market, candle):
    if platform == 'win32':
        market = ''.join(market.split(':'))

    return 'store/envs/%s_%s_%s.pkl' % (exchange, market, candle)


def get_env(exchange: str, market: str, candle: str):
    "Looks up realtime enviroment and re-initializes it"
    fpath = get_pkl_path(exchange, market, candle)
    if os.path.isfile(fpath):
        with open(fpath, 'rb') as f:
            # Re-init realtime enviroment to get updates
            orte = pickle.load(f)
            rte = RealTimeEnv(orte.mi)
            rte.make_trades = True
            rte.fee = 0.999
            return rte
    else:
        return None


def set_env(exchange, market, candle, env):
    "Dump realtime enviroment to file at correct path"
    fpath = get_pkl_path(exchange, market, candle)
    with open(fpath, 'wb+') as f:
        pickle.dump(env, f)


def close_bots(dbs, profile):
    for uid in profile['bots']:
        with dbs['bots'].transact(retry=True):
            bot = dbs['bots'][uid]
            exchange = bot['exchange']
            market = bot['market']

            env = get_env(exchange, market, bot['candles'])
            env.set_user(bot['state'], profile)
            env.nstep()  # Update limit order status from the exchange

            open_orders = bot['state'].open_orders
            for order in list(open_orders.keys()):
                # Cancel on the exchange, and mark as such on our side
                env.get_api().cancel_order(profile['exchanges'][exchange],
                                           market, open_orders[order].txid)
                env._reject_limit(open_orders[order].txid)

            env.sellp(1.)

            bot['enabled'] = False
            bot['stop_time'] = time.time()

            dbs['bots'][uid] = bot


def incr_bot_error(dbs, botid, bot, error):
    # Ignore ratelimiting errors for the users
    if error == 'failed-exchange-ratelimit':
        return

    if 'Binance' in bot['exchange']:
        return

    error_map = {
        'insufficient-balance':
        'Insufficient balance on exchange to make trade',
        'failed-exchange-auth':
        'API Key/Authentication was rejected by exchange'
    }
    strat_meta = StrategyFactory(bot['strategy'], dbs)
    username = bot['user']
    err_key = f"{username}:{botid}:errors"
    errs = dbs['globals'].get(err_key, [])
    errs.append({'timestamp': int(time.time()), 'error': error})

    seconds_per_candle = span_from_candletype(bot['candles'])
    errors_limit = (24 * 60 * 60) / seconds_per_candle  # 4h=6, 1h=24

    if len(errs) > errors_limit:

        def fmtdate(t):
            return datetime.datetime.fromtimestamp(t).strftime(
                "%d/%b/%Y %X UTC")

        title = f"Disabled bot {strat_meta.get_name()} ({bot['market']}) because of errors"
        newline = '<br>'
        body = f"""
Bot {strat_meta.get_name()} ({botid}) was automatically closed because of the following errors:
{newline.join(["%s: %s" % (fmtdate(err['timestamp']), error_map.get(err['error'], 'Unkown Error')) for err in errs])}

You may attempt to recreate the bot after resolving any potential issues. Please contact support if you are unsure what the issue is.
"""
        notification = Notification(title,
                                    body,
                                    'system',
                                    int(time.time()),
                                    icon='error')
        NotificationSaving(dbs).save_notification(notification, username)
        bot['enabled'] = False
        bot['stop_time'] = time.time()
        return

    dbs['globals'].set(err_key, errs, expire=7 * 24 * 24 * 60, retry=True)


def handle_failed_auth(data):
    dbs = get_dbs()
    bot = dbs['bots'][data['uid']]
    username = bot['user']
    if int(time.time()) - dbs['globals'].get(
            f"{username}:{bot['exchange']}:failed-auth", 0) < 2 * 24 * 60 * 60:
        return

    body = f"""
    During one of our trades on {bot['exchange']} the API key/secret was rejected, please check it's permissions and validity of the details you entered, or create a new API key and update it in the settings.
    """
    notification = Notification("Failed to authenticate at exchange",
                                body,
                                'system',
                                int(time.time()),
                                icon='error')
    NotificationSaving(dbs).save_notification(notification, username)
    dbs['globals'].set(f'{username}:{bot["exchange"]}:failed-auth',
                       int(time.time()),
                       expire=7 * 24 * 60 * 60,
                       retry=True)

    NotifyMailPreferences(dbs).notify_mail_preferences(
        username,
        'trade:failed-exchange-auth',
        param={'exchange': bot['exchange']})


def handle_failed_trade(botid, data):
    dbs = get_dbs()
    bot = dbs['bots'][data['uid']]
    username = bot['user']
    strat_meta = StrategyFactory(bot['strategy'], dbs)
    if int(time.time()) - dbs['globals'].get(
            f'{username}:{bot["exchange"]}:insufficient-balance',
            0) < 24 * 60 * 60:
        return
    if data['side'].upper() == 'BUY':
        data['volume'] *= data['price']

    title = f'Action Required: Not Enough Assets on {bot["exchange"]}'
    body = f'Bot {strat_meta.get_name()} ({botid}) does not have enough {data["asset"]} to make a {data["side"]}-{data["type"]} trade on {bot["exchange"]}. It could have happened if you recently manually traded on {bot["exchange"]}. Update your balance on {bot["exchange"]} to have at least {data["volume"]} {data["asset"]}'

    notification = Notification(title,
                                body,
                                'system',
                                int(time.time()),
                                icon='error')
    NotificationSaving(dbs).save_notification(notification, username)

    dbs['globals'].set(f'{username}:{bot["exchange"]}:insufficient-balance',
                       int(time.time()),
                       expire=7 * 24 * 60 * 60,
                       retry=True)

    NotifyMailPreferences(dbs).notify_mail_preferences(
        username,
        'trade:insufficient-balance',
        param={
            'market': data['market'],
            'bot type': strat_meta.get_name(),
            'exchange': bot["exchange"]
        })


def handle_trade_filled(dbs, bot, botid, info):

    def rd(x):
        return round(float(x), 5)

    strat_meta = StrategyFactory(bot['strategy'], dbs)
    tok, cur = bot['market'].split(':')
    change = info.get('change', 0) * 100
    date = datetime.datetime.fromtimestamp(info['date'])
    title = f"{bot['market']} {info['type'].upper()} Executed at {rd(info['price'])} {cur}"

    try:
        info['OBT_fee'] = OBTokenTransaction(dbs).deduct_bot_trade_fee(
            email=bot['user'],
            amount=info['amount'] * info['price'],
            currency=cur).ok() or 0
    except Exception as e:
        info['OBT_fee'] = 0
        logging.error(str(e))

    body = f"""
Bot {strat_meta.get_name()} ({botid}) {'bought' if info['type'].upper() == 'BUY' else 'sold'} {tok} on {bot['exchange']} for total of {round(info['amount'] * info['price'], 2)} {cur} for price of {round(info['price'], 5)} {cur}.<br>
<b>Trade Summary</b><br>
<b>Date:</b> {date:%d-%m-%Y %H:%M:%S} UTC<br>
<b>Exchange:</b> {bot['exchange']}<br>
<b>Market:</b> {bot['market']}<br>
<b>Side:</b> {info['type'].upper()}<br>
<b>Order type:</b> {info['order_type']}<br>
<b>Price:</b> {rd(info['price'])} {cur}<br>
<b>Amount:</b> {rd(info['amount'])} {tok}<br>
<b>Fee:</b> {rd(info.get('fee', 0.)):.9f} {info.get('fee_asset', '')}<br>
<b>OBT Fee:</b> {info['OBT_fee']:.5f} OBT<br>
<b>Bot:</b> {strat_meta.get_name()}<br>
<b>Current bot token balance:</b> {rd(bot['state'].tokBalance)} {tok}<br>
<b>Current bot currency balance:</b> {rd(bot['state'].curBalance)} {cur}<br>
<b>Current bot value:</b> {rd(bot['state'].portfolioValue)} {cur}<br>
<b>Change since last trade:</b> {'' if change < 0 else '+'} {rd(change)} %
    """
    notification = Notification(title,
                                body,
                                'trade',
                                int(time.time()),
                                icon=tok)
    NotificationSaving(dbs).save_notification(notification, bot['user'])

    __total_balance = (rd(info['amount']) + rd(bot['state'].tokBalance))
    __total_balance = 0 if __total_balance == 0 else rd(
        info['amount']) / __total_balance

    notificationHandler = NotificationHandler(dbs)
    message = {
        "bot_type": strat_meta.get_name(),
        "total_balance": __total_balance,
        "market": bot['market'],
        "tok": tok,
        "cur": cur,
        "price": rd(info['price']),
        "type": info['type'].upper(),
        "portfolioValue": rd(bot['state'].portfolioValue),
        "curBalance": rd(bot['state'].curBalance),
        "date": date,
        "change": rd(change)
    }
    if len(bot.get('twitter_tokens', []) or []) == 4:
        notificationHandler.post_tweet(
            bot['twitter_tokens'][0:2], bot['twitter_tokens'][2:4],
            notificationHandler.twitter_message_template(message=message))

    if bot.get('telegram_sent_username', False) and bot.get(
            'telegram_token', False):
        notificationHandler.notify_telegram(
            telegram_token=bot['telegram_token'],
            telegram_sent_username=bot['telegram_sent_username'],
            text=notificationHandler.telegram_message_template(
                message=message),
            username=bot['user'])

    bot['state'].trade_log.append(info)


def handle_stoploss_triggered(dbs, bot, botid, info, env):
    # NOTE: botid is only for reference (do not use it over `bot`)

    if 'auto-stoploss' in bot['features']:
        return

    profile = dbs['users'][bot['user']]
    open_orders = bot['state'].open_orders
    rejected_orders = False
    for order in list(open_orders):
        # Cancel on the exchange, and mark as such on our side
        try:
            env.get_api().cancel_order(profile['exchanges'][bot['exchange']],
                                       bot['market'], open_orders[order].txid)
            if open_orders[order].order_type != 'STOPLOSS':
                env._reject_limit(open_orders[order].txid)
                rejected_orders = True
        except Exception:
            pass

    # Sell any token balance that was still in limit orders (will fail in env if it's too small)
    if rejected_orders and bot['state'].tokBalance > 1e-5:
        env.sell(1.)

    def rd(x):
        return round(float(x), 5)

    strat_meta = StrategyFactory(bot['strategy'], dbs)
    tok, cur = bot['market'].split(':')

    info = info or {'price': np.nan, 'date': time.time(), 'change': 0}
    change = info.get('change', 0) * 100
    date = datetime.datetime.fromtimestamp(info.get('date') or time.time())
    roi = (env.portfolioValue() /
           ((bot['state'].startingBalance[0] * bot['starting_price']) +
            bot['state'].startingBalance[1])) - 1

    title = f"Stop-Loss triggered at {rd(info['price'])} on {bot['market']} for {strat_meta.get_name()}"
    body = f"""
Your bot {strat_meta.get_name()} ({botid}) on {bot['exchange']} {bot['market']} has been archived as it's stop-loss has been triggered.<br>

<b>Summary</b><br>
<b>Date:</b> {date:%d-%m-%Y %H:%M:%S} UTC<br>
<b>Bot:</b> {strat_meta.get_name()}<br>
<b>Exchange:</b> {bot['exchange']}<br>
<b>Market:</b> {bot['market']}<br>
<b>Starting Balance:</b> {rd(bot['state'].startingBalance[1])} {cur}<br>
<b>Ending Balance:</b> {rd(env.portfolioValue())} {cur}<br>
<b>Ending ROI: {rd(roi) * 100}%</b><br>
    """
    notification = Notification(title,
                                body,
                                'system',
                                int(time.time()),
                                icon=tok)
    NotificationSaving(dbs).save_notification(notification, bot['user'])
    bot['enabled'] = False
    bot['stop_time'] = time.time()

    NotifyMailPreferences(dbs).notify_mail_preferences(
        bot['user'],
        'trade:stoploss',
        param={
            'market':
            bot['market'],
            'cur':
            cur,
            'bot type':
            strat_meta.get_name(),
            'token price':
            rd(info['price']),
            'body':
            f''' On {date:%d-%m-%Y %H:%M:%S} UTC  your {bot['market']} {strat_meta.get_name()} on {bot['exchange']} has been archived as it's stop-loss had triggered. 
        The bot started with {rd(bot['state'].startingBalance[1])} {cur} and ended up with {rd(env.portfolioValue())} {cur} balance in use, generating a return on investment(ROI) of {rd(roi) * 100}%'''
        })


def handle_with_clientOrderId_fail(dbs, bot, botid, cid, err_type, err_ctx):
    if not dbs['globals'].get("failed:clientOrders"):
        dbs['globals']["failed:clientOrders"] = dict()

    user_errors = dbs['globals']["failed:clientOrders"].get(bot['user'], [])
    user_errors.append({
        **err_ctx, 'botid': botid,
        'clientOrdersId': cid,
        'err_type': err_type
    })

    dbs['globals']["failed:clientOrders"][bot['users']] = user_errors


def realtime_thread(exchange: str, exchange_cfg: dict):
    setproctitle(f'{getproctitle()} realtime.{exchange}')
    logger = logging.getLogger(f'realtime.{exchange}')
    dbs = get_dbs()
    for candle in exchange_cfg['candles']:
        for market in exchange_cfg['pairs']:
            ran = 0
            total = 0
            llogger = logger.getChild(f'{market}.{candle}')
            env = get_env(exchange, market, candle)

            if env is None:
                llogger.warn('[WARN] RT Env not found')
                continue
            try:
                new_candles, _ = env.update()
                if not new_candles:
                    continue
            except SystemExit:
                return
            except Exception:
                continue

            env.max_buy = 0.99

            for k in dbs['bots']:
                lllogger = llogger.getChild(f'{k}')
                bot = dbs['bots'].get(k, None)
                if not bot:  # Allow for bots to be destroyed while processing
                    continue

                if bot['market'] != market or candle != bot[
                        'candles'] or not bot[
                            'enabled'] or bot['exchange'] != exchange:
                    continue

                total += 1

                # Setup event handling
                fhandle_trade_filled = partial(handle_trade_filled, dbs, bot,
                                               k)
                fhandle_stoploss_triggered = partial(handle_stoploss_triggered,
                                                     dbs, bot, k)
                fhandle_failed_trade = partial(handle_failed_trade, k)

                fhandle_with_clientOrderId_fail = partial(
                    handle_with_clientOrderId_fail, dbs=dbs, bot=bot, botid=k)

                def log_error(err, *args, **kwargs):
                    traceback.print_exc(file=sys.stdout)
                    lllogger.warn("Got error in event handler" + repr(err) +
                                  '\n'.join(map(repr, args)))

                # TODO: use wrapper to automatically remove it after iteration?
                env.on(env.ERROR, log_error)
                env.on('trade:filled', fhandle_trade_filled)
                env.once('trade:insufficient-balance', fhandle_failed_trade)
                env.once('trade:clientOrderId-fail',
                         fhandle_with_clientOrderId_fail)
                env.once('trade:failed-exchange-auth', handle_failed_auth)

                def _on_err(err_ctx):
                    incr_bot_error(dbs, k, bot, err_ctx['error'])

                #env.once('trade:fail-exchange', _on_err)
                env.once('trade:stoploss', fhandle_stoploss_triggered)

                # Run the strategy in env
                try:
                    fab = StrategyFactory(bot['strategy'], dbs)
                    strat = fab.construct(env)
                    if getattr(strat, 'loads', None):
                        strat.loads(bot.get('internal_state', b''))

                    profile = dbs['users'][bot['user']]
                    env.set_user(bot['state'], profile)
                    env.logger = lllogger

                    # Step (check orders/positions)
                    lllogger.info(
                        f"Running {type(strat).__name__}: {bot['state'].portfolioValue} <- tok: {bot['state'].tokBalance}, cur: {bot['state'].curBalance}"
                    )
                    env.nstep()

                    # Bot might have been disabled during nstep (stoploss)
                    if bot['enabled']:
                        if new_candles:
                            action = strat.step()
                            # Pre-emptive save in case of SystemExit
                            dbs['bots'][k] = bot
                            lllogger.debug(
                                f"Raw model output: action={action}")

                        if bot.get('starting_price', None) == None:
                            bot['starting_price'] = env.current_v()

                        if getattr(strat, 'dumps', None):
                            bot['internal_state'] = strat.dumps()

                        if (bot.get('stop_loss') or {}).get('stop'):
                            # Fill in portfolio for older bots
                            bot['stop_loss']['starting_portfolio'] = bot[
                                'stop_loss'].get('starting_portfolio') or bot[
                                    'state'].startingBalance[1]

                            if bot['stop_loss'].get('trailing', False):
                                # Track highest portfolio since enabling stoploss
                                current_portfolio = env.portfolioValue()
                                if bot['stop_loss'][
                                        'highest_portfolio'] < current_portfolio:
                                    bot['stop_loss'][
                                        'highest_portfolio'] = current_portfolio

                                top_roi = bot['stop_loss'][
                                    'highest_portfolio'] / bot['stop_loss'][
                                        'starting_portfolio']
                                stop_loss_frac = top_roi * bot['stop_loss'][
                                    'stop']
                            else:
                                stop_loss_frac = bot['stop_loss']['stop']

                            # Take into account the balance in orders for the price, but not for the actual order
                            cur, tok = bot['state'].curBalance, bot[
                                'state'].tokBalance
                            for order in bot['state'].open_orders.values():
                                if order.side.upper() == 'SELL':
                                    tok += order.volume
                                elif order.side.upper() == 'BUY':
                                    cur += order.volume * order.price

                            # Only check stoploss if our currency is not enough to reach the target ROI
                            if stop_loss_frac * bot['stop_loss'][
                                    'starting_portfolio'] > cur:
                                # Stop-price factors in current position in order to reach the target ROI
                                # If stop-price > current price, close immidiately
                                # stop_roi = (tokBalance * price + curBalance)/startBalance
                                stop_price = (
                                    (stop_loss_frac *
                                     bot['stop_loss']['starting_portfolio']) -
                                    cur) / (tok + 1e-8)
                                if env.current_v() < stop_price:
                                    # Already below target price, just stop the bot
                                    tx = env.sell(0.99)
                                    env.emit('trade:stoploss', tx, env)
                                else:
                                    env._stop_order(
                                        bot['state'].tokBalance * 0.99,
                                        stop_price)

                    bot['bah_roi'] = (env.current_v() - bot['starting_price']
                                      ) / bot['starting_price']
                    bot['state'].last_trade_attempt = time.time()

                except SystemExit:
                    return
                except Exception as e:
                    lllogger.warn(
                        f'Failed to run bot {str(e)} {k} {market} {candle} {exchange}'
                    )
                    traceback.print_exc(file=sys.stdout)
                finally:
                    # Detach event handlers
                    env.off(env.ERROR)
                    env.off(listener=fhandle_trade_filled)
                    env.off(listener=fhandle_stoploss_triggered)
                    env.off(listener=fhandle_failed_trade)
                    env.off(listener=handle_failed_auth)
                    env.off(listener=_on_err)

                # Update any changes made to state et al
                ran += 1
                dbs['bots'][k] = bot

            set_env(exchange, market, candle, env)
            llogger.info('RT bots candles: %d, ran %d/%d bots' %
                         (new_candles, ran, total))


class RealTimeEvl:

    def __init__(self, dbs, exchange_map):
        self.dbs = dbs
        self.exchanges = exchange_map
        self.dir = 'store/envs/'
        self.load_envs()

    def load_envs(self):
        print('Loading rt envs')
        for exchange in self.exchanges:
            for market in self.exchanges[exchange]['pairs']:
                for candle in self.exchanges[exchange]['candles']:
                    fpath = get_pkl_path(exchange, market, candle)
                    if os.path.isfile(fpath):
                        try:
                            print(
                                f'Checking enviroment for {exchange} {market} {candle}'
                            )
                            with open(fpath, 'rb') as f:
                                # Load RT env to check for integrity
                                pickle.load(f)
                                continue
                        except SystemExit:
                            raise
                        except:
                            print(
                                'Integrity check failed, rebuilding enviroment'
                            )

                    print(f'Downloading {exchange} {market} {candle}')
                    try:
                        mi = MarketInfo.from_download(market,
                                                      candle,
                                                      exchange=exchange)
                        rte = RealTimeEnv(mi)
                        # Store permanently
                        with open(fpath, 'wb+') as f:
                            pickle.dump(rte, f)
                    except SystemExit:
                        raise
                    except Exception:
                        print(f"Failed to load {exchange} {market} {candle}")
                        traceback.print_exc(file=sys.stdout)
            print(f'Loading rt for {exchange} finished')

    def is_reload_env(self):
        if self.dbs['globals'].get('RealTimeEvl:is_reload_env', False):
            self.exchanges = Exchange(self.dbs).get_exchange_configs()
            self.load_envs()
            self.dbs['globals']['RealTimeEvl:is_reload_env'] = False

    def loop(self):
        with Pool(os.environ.get("RT_THREADS", 4)) as p:
            p.starmap(realtime_thread,
                      [(x, self.exchanges[x]) for x in self.exchanges])
        self.is_reload_env()
