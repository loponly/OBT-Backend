import time
import requests
import logging
from result import Ok,Err,Result
from routes.utils import memorize


def call(url,method, query={},**kwarks) -> Result:
    headers = {
    'accept': 'application/json',
    }

    try:
        res = requests.request(method=method,url=url,params=query,headers=headers,**kwarks)
        if res.status_code == 200:
            return Ok(res.json())

        return Err({'error':res.status_code,'context':f"sol-{url}-{query}-{res.content}"})
    except Exception as e:
        return Err({'error':f"sol-{url}-{query}-{str(e)}"})


class SolanaApi():

    base_url = ''
    token_symbol = 'OBBOT'

    def __init__(self,dbs) -> None:
        self.dbs = dbs


    def _get_nft_tokens(self,owner_address:str)->dict:
        res = call(url="https://public-api.solscan.io/account/tokens",method="GET",query={"account":owner_address})
        if res.is_err():
            return []
        return list(filter(lambda d: d if int(d.get('tokenAmount',{}).get('amount',0))> 0 else False,res.ok()))


    def get_token_info(self,token_address:str)->dict:
        _return = {}
        if self.dbs['nft_token_bots'].get(token_address):
            result = self.dbs['nft_token_bots'][token_address]
            if 'error' in result:
                return _return
            return result
            

        res = call(url='https://api.solscan.io/account',method="GET",query={"address":token_address})
        if res.is_err():
            return {}
        data= res.ok().get('data',{})

        metadata_uri = data.get('metadata',{}).get('data',{}).get('uri',None)
        if data.get('tokenInfo',{}).get('symbol','') != self.token_symbol and not metadata_uri:
            self.dbs['nft_token_bots'][token_address] = {'error':'Not OBT NFT'}
            return _return

        res = call(url=metadata_uri,method="GET")
        if res.is_err():
            return _return


        token_data = res.ok()
        self.dbs['nft_token_bots'][token_address] = token_data

        return token_data
        
    def get_nft_tokens(self,owner_address:str)->list:
        nft_tokens  = self._get_nft_tokens(owner_address)
        _return = set()
        for nft_address in nft_tokens:
            if not nft_address.get('tokenAddress'):
                continue
            _tokenAddress = nft_address['tokenAddress']
            _return.add(_tokenAddress)
        return list(_return)

    def update_all_users_owned_nfts(self):

        for u in self.dbs['users']:
            self.update_user_owned_nfts(u)

    @memorize
    def get_skin(self,token_address):
        token_info = self.get_token_info(token_address)
        if token_info:
            _name = token_info.get('attributes',[{}])[-1].get('value','')
            if _name:
                return ' '.join(_name.split(' ')[:-1]).lower()
        return None


    def update_user_owned_nfts(self,u:str):
        try:
            profile = self.dbs['users'][u]
            if not profile.get('obt_token',{}).get('NFT',{}).get('address'):
                return
            new_token_addresses = set(self.get_nft_tokens(profile['obt_token']['NFT']['address']))  
            
            current_token_addresses = profile.get('obt_token',{}).get('NFT',{}).get('token_address',{})
            if isinstance(current_token_addresses,list):
                current_token_addresses =  {}

            _current_token_addresses = set(current_token_addresses.keys())
            
            remove_difference = _current_token_addresses-new_token_addresses
            for d in remove_difference:
                del current_token_addresses[d]
            
            diffrence = new_token_addresses - _current_token_addresses
            for _d in diffrence:
                current_token_addresses[_d] = time.time()

            
            profile['obt_token']["NFT"]['token_address'] = current_token_addresses
            self.dbs['users'][u] = profile
            logging.info(u)
        except Exception as e:
            logging.error(str(e))
        

    @memorize
    def get_image_urls(self,token_addresses:dict)->dict:
        _return = {}
        for d in token_addresses:
            _d = self.get_token_info(d)
            _name = _d.get('attributes',[{}])[-1].get('value','')
            if _name:
                __name = _name.split(' ')[-1].lower()
                _return[__name]= _return.get(__name,{})
                _return[__name][_d.get('name','').strip()] = _d.get('image')

        return _return

    @memorize
    def get_all_token_infos(self,token_addresses:dict,tiers:dict) -> dict:

        _return  = []
        if not token_addresses:
            return _return
        for d in token_addresses:
            _d = self.get_token_info(d)
            _name_type = _d.get('attributes',[{}])[-1].get('value','')
            if _d and _name_type:
                _return.append({
                    'number_in_collection': _d.get('name','').strip(),
                    'image_url': _d.get('image'),
                    'skin':' '.join(_name_type.split(' ')[:-1]).lower(),
                    'bot_type': _name_type.split(' ')[-1].lower(),
                    'token_address':d,
                    'tier':tiers.get(d,{}).get('tier',{})
                })
        
        return _return


    