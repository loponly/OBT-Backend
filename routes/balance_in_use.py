from .base import Route, auth_guard

class BalanceInUse(Route):
    @auth_guard
    def on_get(self, req, resp):
        days = req.params.get('days', None)
        username = self.get_username(req).unwrap()
        data = self.dbs['balance_in_use'].get(username, {})
        if not days:
            resp.media = {'values': data}
            return
        amount = int(days) * 24
        temp = list(data.items())
        values = temp[-amount:]
        resp.media = {'values': dict(values)}
