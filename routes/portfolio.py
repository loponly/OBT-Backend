import falcon
from .base import Route, auth_guard
from .spectree import spectree
from spectree import Response
from pydantic import BaseModel
from typing import Dict, Optional

class PortfolioValueGetReq(BaseModel):
    days: Optional[int]

class PortfolioValueGetResp(BaseModel):
    values: Dict[int, Dict[str, float]]

class PortfolioValue(Route):
    @auth_guard
    @spectree.validate(query=PortfolioValueGetReq, resp=Response(HTTP_200=PortfolioValueGetResp))
    def on_get(self, req, resp):
        self.mark_activity(req)
        days = req.params.get('days', None)
        username = self.get_username(req).unwrap()
        if not days:
            resp.media = {'values': self.dbs['profile_portfolios'][username]}
            return
        amount = int(days) * 24
        portfolios = self.dbs['profile_portfolios'][username]
        temp = list(portfolios.items())
        values = temp[-amount:]
        resp.media = {'values': dict(values)}
