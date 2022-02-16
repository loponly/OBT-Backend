from datetime import datetime
import logging
import falcon
from routes.utility.solana_api import SolanaApi
from routes.utility.acl import ACLManager
from functools import reduce

from routes.utility.ob_token import  OBToken, get_price_in_usd

from .spectree import spectree
from .base import Route, auth_guard
from routes.utility.users import UserManager

from pydantic import BaseModel, constr
from typing import List, Optional, Any, Dict

class NFTBotsReq(BaseModel):
    nft_address: str
    signature: str
    wallet_type: str

class NFTBotImageSelectReq(BaseModel):
    strategy_name : str
    selected_image_name : str
class NFTWallet(BaseModel):
    metamask_address: constr(regex=r'0x[a-fA-F0-9]{40}')
    address: constr(regex=r'[a-zA-Z0-9]{44}')
    is_lock: bool

"""
globals|nft:eligible -> Set[str]
globals|nft:lock_cost -> int
globals|nft:free_mints -> Optional[Dict[str,str]]
"""

class NFTLoyality(Route):
    @auth_guard
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]

        metamask_addr = req.media['metamask_address']
        eligible_addresses = self.dbs['globals'].get('nft:eligible', set())
        resp.media = {'free_mint': metamask_addr in eligible_addresses}


class NFTWhitelist(Route):
    @auth_guard
    def on_get(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        if not profile.get('OTP_config', {}).get('is_verified'):
            resp.media = {'error': f"Please enable your 2 factor authentication."}
            resp.status = falcon.HTTP_400
            return

        profile = UserManager(self.dbs).ensure_token_address(profile, username)
        address = profile['obt_token']['address'].pub
        nft = profile['obt_token'].get('NFT')
        if not nft:
            resp.media = {'error': f"Please enable your NFT address"}
            resp.status = falcon.HTTP_400
            return


        resp.media = {
            'wallet_address': address,
            'obt_usd_price': get_price_in_usd(self.dbs), 
            'NFT': nft,
            'balance': profile['obt_token']['balance']
        }

        resp.status = falcon.HTTP_200

    @auth_guard
    @spectree.validate(json=NFTWallet)
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]

        if not profile.get('OTP_config', {}).get('is_verified'):
            resp.media = {'error': f"Please enable your 2 factor authentication."}
            resp.status = falcon.HTTP_400
            return

        metamask_addr = req.media['metamask_address']

        profile = UserManager(self.dbs).ensure_token_address(profile, username)
        address = profile['obt_token']['address'].pub
 
        lock_amount = self.dbs['globals'].get('nft:lock_cost', 30)*OBToken.token_decimal
        obt_balance = profile['obt_token']['balance']

        if profile['obt_token'].get('NFT',{}).get('lock_amount'):
            resp.media = {
                'wallet_address': address,
                'obt_usd_price': get_price_in_usd(self.dbs), 
                'NFT':profile['obt_token']['NFT'],
                'balance': obt_balance,
                'status': 'Already Submitted'
            }
            resp.status = falcon.HTTP_200
            return

        if obt_balance < lock_amount:
            resp.media = {'error': f"Please provide {(lock_amount-obt_balance)//OBToken.token_decimal} OBT to start lock your balance."}
            resp.status = falcon.HTTP_400
            return

        profile['obt_token']['NFT'] = {
            'address': req.media['address'],
            'lock_amount': lock_amount,
            'unlock_date': int(datetime(2022,1,1,0,0,0).timestamp()),
            'date': int(datetime.now().timestamp()),
        }

        eligible_mints = self.dbs['globals']['nft:eligible']
        free_mints = self.dbs['globals'].get('nft:free_mints', {})

        if metamask_addr in eligible_mints:
            free_mints[metamask_addr] = req.media['address']
        
        self.dbs['globals']['nft:free_mints'] = free_mints
        self.dbs['users'][username] = profile

        resp.media = {
            'wallet_address': address,
            'obt_usd_price': get_price_in_usd(self.dbs), 
            'NFT': profile['obt_token']['NFT'],
            'balance': obt_balance,
            'status': 'Submitted'
        }

        resp.status = falcon.HTTP_200

class NFTBotsTokenNetworkRestart(Route):

    @auth_guard
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        resp.media = 'Refreshed!'
        resp.status = falcon.HTTP_200
        SolanaApi(self.dbs).update_user_owned_nfts(username)
        


class NFTBotsToken(Route):

    
    @auth_guard
    @spectree.validate(json=NFTBotsReq)
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]

        nft_address = req.media['nft_address']
        signature = req.media['signature']
        wallet_type = req.media['wallet_type']
        
        if not profile.get('obt_token'):
            profile['obt_token'] = {}
            self.dbs['users'][username] = profile

        if not profile['obt_token'].get('NFT',{}):
            profile['obt_token']['NFT'] = {}
        
        if nft_address in [self.dbs['users'][u].get('obt_token',{}).get('NFT',{}).get('address') for u in self.dbs['users']]:
            resp.media = {'error': f'Your {nft_address} is not allowed! It is already used by other user!'}
            resp.status =falcon.status.HTTP_400
            return
            
        profile['obt_token']['NFT']['address'] = nft_address
        profile['obt_token']['NFT']['wallet_type'] = wallet_type
        profile['obt_token']['NFT']['signature'] = signature
        self.dbs['users'][username] = profile
        resp.media =  profile['obt_token']['NFT']

    @auth_guard
    def on_get(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]

        if not profile.get('obt_token'):
            profile['obt_token'] = {}
            self.dbs['users'][username] = profile

        if not profile['obt_token'].get('NFT',{}).get('address'):
            resp.media = {'error': 'No NFT address found!'}
            resp.status = falcon.HTTP_400
            return

        if not profile['obt_token']["NFT"].get('token_address'):
            resp.media = {
                    'NFT_token_bots': {},
                    'owner_address': profile['obt_token']["NFT"]['address'],
                    'wallet_type':profile['obt_token']["NFT"].get('wallet_type'),
                    }
            return

        sol = SolanaApi(self.dbs)
        token_addresses =profile['obt_token']["NFT"].get('token_address')
        skin_tier = ACLManager(self.dbs).get_skins_tier(token_addresses)
    
        token_infos = sol.get_all_token_infos(token_addresses,skin_tier)

        resp.media = { 'NFT_token_bots':token_infos,
                        'NFT_select_bot_images':profile.get('obt_token',{}).get('NFT',{}).get('token_images',{}),
                        'owner_address':profile['obt_token']["NFT"]['address'],
                        'wallet_type':profile['obt_token']["NFT"].get('wallet_type'),
                        'total_bots_allowed':reduce(lambda p,d: p+ skin_tier[d].get('tier',{}).get('allowed_bots',0),skin_tier,0)
                        }

    @auth_guard
    @spectree.validate(json=NFTBotImageSelectReq)
    def on_put(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]

        strategy_name = req.media['strategy_name'].lower()
        selected_image_name = req.media['selected_image_name']

        if not profile.get('obt_token'):
            profile['obt_token'] = {}
            self.dbs['users'][username] = profile


        if not profile['obt_token'].get('NFT',{}).get('address'):
            resp.media = {'error': 'No NFT address found!'}
            resp.status = falcon.HTTP_400
            return

        if not profile['obt_token']["NFT"].get('token_address'):
            resp.media = {'error': 'No NFT mint or updateting the stats!'}
            resp.status = falcon.HTTP_400
            return
        
        if not selected_image_name and profile['obt_token']["NFT"].get("token_images",{}).get(strategy_name):
            del profile['obt_token']['NFT']['token_images'][strategy_name]
            self.dbs['users'][username] = profile
            resp.media = profile['obt_token']['NFT']['token_images']
            return

        nft_images = SolanaApi(self.dbs).get_image_urls(profile.get('obt_token',{}).get('NFT',{}).get('token_address',{}))

        if selected_image_name not in nft_images.get(strategy_name,{}):
            resp.media = {'error': f'Image {selected_image_name} is not available from your Inventory.'}
            resp.status = falcon.HTTP_400
            return

        profile['obt_token']['NFT']['token_images'] = profile['obt_token']['NFT'].get('token_images',{})
        profile['obt_token']['NFT']['token_images'][strategy_name] = selected_image_name
        self.dbs['users'][username] = profile

        resp.media = {'NFT_select_bot_images':profile['obt_token']['NFT']['token_images']}

    @auth_guard
    def on_delete(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]

        if not profile.get('obt_token',{}).get('NFT'):
            resp.media = {'error': 'No NFT mint or updateting the stats!'}
            resp.status = falcon.HTTP_400
            return

        profile['obt_token']['NFT'] = {}
        self.dbs['users'][username] = profile
        resp.media = 'Success!'

