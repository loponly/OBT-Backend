import falcon
from .base import Route, auth_guard
from .spectree import spectree
from spectree import Response
from pydantic import BaseModel
from typing import Dict, Optional


class ProfileProfitGetReq(BaseModel):
    days: Optional[int]

class ProfileProfitGetResp(BaseModel):
    __root__: Dict[int, float]

class ProfileProfit(Route):
    @auth_guard
    @spectree.validate(query=ProfileProfitGetReq, resp=Response(HTTP_200=ProfileProfitGetResp))
    def on_get(self, req, resp):
        days = req.params.get('days', None)
        username = self.get_username(req).unwrap()

        result = self.dbs['profile_profits'].get(username, {})
        if days:
            amount = int(days) * 24
            temp = list(result.items())
            result = temp[-amount:]
            result = dict(result)
        
        keys_values = result.items()
        result = {str(key): value for key, value in keys_values}

        resp.media = result
