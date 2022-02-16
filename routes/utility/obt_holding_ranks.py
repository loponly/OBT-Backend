import requests
import logging
from bs4 import BeautifulSoup

from routes.utility.token import OBToken




class OBTHoldingRanks:
    token = '0x8dA6113655309f84127E0837fcf5C389892578B3'

    exlude_address = {
        '0x2ebaa4f053fced95c313d54288ae6914e3dc7afc',
        '0xa36696ce73a5252681a9596dd5b2229dd8557922',
        '0x70c547e6458e10c53b2ded801ccb3f31078d3fce',
        '0x556bc7809f38f42d4fe431e971b4e705703aa7ce',
        '0x3c2ec339bd9ba87c54454f819faf82a9b0c9ae44',
        '0x846f2aa2e0bc8796d0df698abf0674242a7302f8'
    }
    
    def __init__(self,dbs) -> None:
        self.dbs = dbs

    def get_ranks(self,page=1):
        headers = {
            'Referer': f'https://bscscan.com/token/{self.token}',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/88.0.4324.150 Safari/537.36',
        }


        params = {
            "m": "normal",
            "a": self.token,
            "p": page,
        }

        response = requests.get(
            "https://bscscan.com/token/generic-tokenholders2", headers=headers, params=params)

        element = BeautifulSoup(response.content,"html.parser")

        ranks = []
        for row in element.select("tr:has(td)"):
            tds = [td.get_text(strip=True) for td in row.select("td")]
            if len(tds) == 5:
                try:
                    ranks.append({
                        'address':row.select('td span a[href]')[-1]['href'].split("=")[-1],
                        'rank': int(tds[0]),
                        'balance': int(float(tds[2].replace(',',''))*OBToken.token_decimal)
                    })

                except ValueError as e:
                    logging.error(str(e))
                    continue

        return ranks

    def request_all_the_ranks_from_bscan(self):

        p = 1
        data = []
        while True:
            try:
                result = self.get_ranks(page=p)
                if not result:
                    break

                data.extend(result)
                p += 1
            except Exception as e:
                logging.error(str(e))
                return []

        return {d.get('address',''):d.get('balance',0) for d in data}

    def refresh_all_the_ranks(self):

        data = self.request_all_the_ranks_from_bscan()
        if data:
            for u in self.dbs['users']:
                profile = self.dbs['users'][u]
                if profile.get('obt_token',{}).get('address',None):
                    address = profile.get('obt_token',{}).get('address',{}).pub
                    data[address] = data.get(address,0) + int(profile.get('obt_token',{}).get('balance',0))
            
            _data = data.copy()
            for k in _data:
                if k in self.exlude_address:
                    del data[k]

            self.dbs['globals'][f"{type(self).__name__}:ranks"] = {d[0]:i+1 for i,d in enumerate(list(sorted(data.items(), key=lambda item: item[1],reverse=True)))}
            return True


        return False
        

    
