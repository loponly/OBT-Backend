import falcon
import os
import traceback
from falcon_cors import CORS
from routes.base import get_dbs
from routes.auth import *
from routes.backtesting import *
from routes.nft_whitelist import *
from routes.strategy import *
from routes.exchange import *
from routes.bots import *
from routes.botstats import *
from routes.invitations import *
from routes.portfolio import *
from routes.notifications import *
from routes.preferences import *
from routes.admin import *
from routes.users import *
from routes.public import *
from routes.public_bots import *
from routes.bot_profit import *
from routes.spectree import spectree
from routes.balance_in_use import *
from routes.payment import *
from routes.stripehooks import *
from routes.profile import ReferralInfo
import sentry_sdk
from routes.sentry import FalconIntegration


class HTMLRouting:
    def __init__(self):
        if os.path.exists('public/index.html'):
            with open('public/index.html', 'rb') as f:
                self.index_html = f.read()
        else:
            self.index_html = '<b>Something went wrong, make sure javascript is enabled; please contact support if the issue persists</b>'

    def process_request(self, req, resp):
        if req.path[-1] == '/':
            req.path += 'index.html'

    def process_response(self, req, resp, resource, req_succeeded):
        resp.set_header('X-Frame-Options', 'SAMEORIGIN')
        resp.set_header('X-Content-Type-Options', 'nosniff')
        resp.set_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        if not req.path.startswith('/api') and '404' in resp.status:
            print(req.path, resp.status)
            resp.content_type = falcon.MEDIA_HTML
            resp.data = self.index_html
            resp.status = falcon.HTTP_200


def handle_assertions(req, resp, ex, params):
    # traceback.print_exc()
    raise falcon.HTTPBadRequest(
        description=str(ex)
    )


def create_api(prod=False):
    dbs = get_dbs()

    if os.environ.get('ENVIRONMENT', 'dev') not in ['prod', 'staging']:
        cors = CORS(
                allow_all_origins=True,
                allow_all_headers=True,
                allow_all_methods=True,
                allow_methods_list=['GET', 'POST', 'DELETE', 'PUT'])
    else:
        cors = CORS(allow_origins_list=['https://onebutton.trade', 'https://www.onebutton.trade', 'https://script.google.com', 'https://n-7dddh4ljwfpdbze6k4drdy5xyg2iovnh7fy7bby-0lu-script.googleusercontent.com/'], allow_all_headers=True, allow_all_methods=True)

    sentry_sdk.init(
        dsn="https://fa4f055b782f4310ba4503439e6570f2@o494993.ingest.sentry.io/5567001",
        integrations=[FalconIntegration()],
        environment=os.environ.get('ENVIRONMENT', "dev")
    )

    api = falcon.App(middleware=[cors.middleware, HTMLRouting()])

    formhandler = api.req_options.media_handlers.get('multipart/form-data')
    formhandler.parse_options.max_body_part_buffer_size = 1024 * 1024 * 10  # 30MiB

    api.add_static_route('/', os.path.join(os.getcwd(), 'public'))
    api.add_static_route('/dataset', os.path.join(os.getcwd(), 'store', 'dataset'))
    api.add_static_route('/unique', os.path.join(os.getcwd(), 'store', 'unique'))
    api.add_route('/api/v1/backtest', Backtest(dbs))
    api.add_route('/api/v1/backtestoptions', BacktestOptions(dbs))
    api.add_route('/api/v1/strategy', Strategies(dbs))
    api.add_route('/api/v1/strategyoptions', StrategyOptions(dbs))
    api.add_route('/api/v1/availablestrategies', AvailableStrategies(dbs))
    api.add_route('/api/v1/connectexchange', ConnectExchange(dbs))
    api.add_route('/api/v1/getexchanges', GetExchanges(dbs))
    api.add_route('/api/v1/exchangedata', ExchangeData(dbs))
    api.add_route('/api/v1/createbot', CreateBot(dbs))
    api.add_route('/api/v1/botstats', GetBotStats(dbs))
    api.add_route('/api/v1/stats/getbotradelogs', GetBoTradeLogs(dbs))
    api.add_route('/api/v1/stats/getbotbalancedistrbution', GetBotBalanceDistrbution(dbs))
    api.add_route('/api/v1/stats/gettradedistribution', GetTradeDistribution(dbs))
    api.add_route('/api/v1/cumulativestats', BotStatsCumulativeStats(dbs))
    api.add_route('/api/v1/profilesummary', GetProfileSummary(dbs))
    api.add_route('/api/v1/botlogs', BotLogs(dbs))
    api.add_route('/api/v1/bot/estimateobtfee', EstimateOBTFee(dbs))
    api.add_route('/api/v1/profilebots', ProfileBots(dbs))
    api.add_route('/api/v1/positionsize', PositionSize(dbs))
    api.add_route('/api/v1/invitation', Invitations(dbs))
    api.add_route('/api/v1/portfolios', PortfolioValue(dbs))
    api.add_route('/api/v1/notifications', Notifications(dbs))
    api.add_route('/api/v1/requests', Requests(dbs))
    api.add_route('/api/v1/botportfolios', BotPortfolio(dbs))
    api.add_route('/api/v1/version', Version(dbs))
    api.add_route('/api/v1/notifpreferences', NotificationPreferences(dbs))
    api.add_route('/api/v1/mailingpreferences', MailingPreferences(dbs))
    api.add_route('/api/v1/publicstrategies', PublicStrategies(dbs))
    api.add_route('/api/v1/publicsroi', PublicROI(dbs))
    api.add_route('/api/v1/highestroi', HighestROI(dbs))
    api.add_route('/api/v1/profileprofit', ProfileProfit(dbs))
    api.add_route('/api/v1/listbots', GetBotsCategorized(dbs))
    api.add_route('/api/v1/botvisible', BotVisible(dbs))
    api.add_route('/api/v1/balanceinuse', BalanceInUse(dbs))
    api.add_route('/api/v1/readallnotifications', ReadAllNotifications(dbs))
    api.add_route('/api/v1/referralinfo', ReferralInfo(dbs))

    api.add_route('/api/v1/token/balance', TokenBalance(dbs))
    api.add_route('/api/v1/token/requestwithdraw', RequestWithdraw(dbs))
    api.add_route('/api/v1/token/estimatefee', EstimateFee(dbs))
    api.add_route('/api/v1/token/transaction', TokenTransaction(dbs))
    api.add_route('/api/v1/token/otp/enable', EnableOTP(dbs))
    api.add_route('/api/v1/token/otp/secret_question', OTPQuestions(dbs))
    api.add_route('/api/v1/nftwhitelist', NFTWhitelist(dbs))
    api.add_route('/api/v1/nfteligible', NFTLoyality(dbs))
    api.add_route('/api/v1/nft/botstoken', NFTBotsToken(dbs))
    api.add_route('/api/v1/nft/restart', NFTBotsTokenNetworkRestart(dbs))

    api.add_route('/api/v1/login', Authenticate(dbs))
    api.add_route('/api/v1/login_with_google', GoogleAuthenticate(dbs))
    api.add_route('/api/v1/login/checkotp', CheckAutheticationOTP(dbs))
    api.add_route('/api/v1/forgotpassword', ForgotPassword(dbs))
    api.add_route('/api/v1/resetpassword', ResetPassword(dbs))

    api.add_route('/api/v1/admin/auth', MetaAuth(dbs))
    api.add_route('/api/v1/admin/info', AdminInfo(dbs))
    api.add_route('/api/v1/admin/logs', AdminLogs(dbs))
    api.add_route('/api/v1/admin/users', AdminUsers(dbs))
    api.add_route('/api/v1/admin/strategies', AdminStrategies(dbs))
    api.add_route('/api/v1/admin/stats', AdminStats(dbs))
    api.add_route('/api/v1/admin/requests', AdminRequests(dbs))
    api.add_route('/api/v1/admin/denyrequest', AdminDenyRequests(dbs))
    api.add_route('/api/v1/admin/invitations', AdminInvitations(dbs))
    api.add_route('/api/v1/admin/revokeinvitation', AdminRevokeInvitation(dbs))
    api.add_route('/api/v1/admin/migratestrategy', AdminStrategyTransport(dbs))
    api.add_route('/api/v1/admin/deleteuser', AdminDeleteUsers(dbs))
    api.add_route('/api/v1/admin/adminbots', AdminBotList(dbs))
    api.add_route('/api/v1/admin/admininactivebots', AdminBotListInactive(dbs))
    api.add_route('/api/v1/admin/analytics', AdminAnalytics(dbs))
    api.add_route('/api/v1/admin/usersubscriptions', AdminUserSubscriptions(dbs))
    api.add_route('/api/v1/admin/subscriptionconfig', AdminSubscriptionConfig(dbs))
    api.add_route('/api/v1/admin/holdingtiersconfig', AdminHoldingTiersConfig(dbs))
    api.add_route('/api/v1/admin/nfttiersconfig', AdminNFTTiersConfig(dbs))
    api.add_route('/api/v1/admin/commonbenefits', AdminSubscriptionBenefits(dbs))
    api.add_route('/api/v1/admin/deleteduserslogs', AdminDeleteUsersLog(dbs))
    api.add_route('/api/v1/admin/promotingorder', AdminPromotingOrder(dbs))
    api.add_route('/api/v1/admin/resurrectbot', AdminResurrectBot(dbs))
    api.add_route('/api/v1/admin/exchangemarket', AdminExchangeMarket(dbs))
    api.add_route('/api/v1/admin/adminbotanalytics', AdminBotAnalytics(dbs))
    # api.add_route('/api/v1/admin/adminobtearning', AdminOBTEarningAnalytics(dbs))
    api.add_route('/api/v1/admin/disableotp', AdminDisable2FA(dbs))
    api.add_route('/api/v1/admin/referral', AdminReferral(dbs))

    api.add_route('/api/v1/users/changename', UserChangeName(dbs))
    api.add_route('/api/v1/users/changepassword', UserChangePassword(dbs))
    api.add_route('/api/v1/users/deleteuser', DeleteUser(dbs))
    api.add_route('/api/v1/users/getrank', UserOBTRank(dbs))

    api.add_route('/api/v1/paymentcustomers', StripeCustomers(dbs))
    api.add_route('/api/v1/paymentsubscriptions', StripeSubscription(dbs))
    api.add_route('/api/v1/paymentsetup', StripeSetupIntent(dbs))
    api.add_route('/api/v1/paymentmethods', StripePaymentMethods(dbs))
    api.add_route('/api/v1/payment/feepertrade', FeePerTrade(dbs))
    api.add_route('/api/v1/paymentbots', BotPaymentDetails(dbs))
    api.add_route('/api/v1/stripehooks', StripeHooks(dbs))
    api.add_route('/api/v1/availablesubscriptions', AvailableSubcriptions(dbs))
    api.add_route('/api/v1/currentholdingtier', CurrentHoldingTier(dbs))
    api.add_route('/api/v1/allholdingtiers', AllHoldingTier(dbs))
    api.add_route('/api/v1/subscription', UserSubscription(dbs))
    api.add_route('/api/v1/invoices', UserInvoices(dbs))

    api.add_error_handler(AssertionError, handle_assertions)

    return api


if __name__ == "server":
    # TODO: set flag in production
    api = create_api()
    spectree.register(api)
    # watchdog_proc = multiprocessing.Process(target=watchdog)
    # watchdog_proc.start()

    # ## Clean up before exiting
    # import atexit

    # def exit_handler():
    #     watchdog_proc.terminate()

    # atexit.register(exit_handler)
