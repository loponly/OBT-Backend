import falcon
from spectree import Response
from pydantic import BaseModel
from typing import Dict, Optional

from .base import Route, auth_guard
from .spectree import spectree


class NotificationsGetReq(BaseModel):
    page: Optional[int]
    category: Optional[str]

class NotificationModel(BaseModel):
    title: str
    text: str
    category: str
    timestamp: int
    icon: Optional[str]
    glob: bool
    link: Optional[str]
    seen: bool

class NotificationsGetResp(BaseModel):
    result: Dict[str, NotificationModel]
    unseen: Dict[str, int]
    pages: int

class NotificationsPostReq(BaseModel):
    notification_id: str

class NotificationsPostResp(BaseModel):
    success: bool = True

notif_categories = ['trade', 'system']

class Notifications(Route):
    @auth_guard
    @spectree.validate(query=NotificationsGetReq, resp=Response(HTTP_200=NotificationsGetResp))
    def on_get(self, req, resp):
        self.mark_activity(req)
        username = self.get_username(req).unwrap()
        profile = self.get_profile(req).unwrap()
        page = int(req.params.get('page', 1)) - 1
        category = req.params.get('category', None)

        notifs = self.dbs['notifications'].get(username, {})
        notifkeys = list(notifs)

        if category:
            notifkeys = list(filter(lambda x: notifs[x]['category'] == category, notifkeys))
        notifkeys = list(sorted(notifkeys, key=lambda x: -notifs[x]['timestamp']))

        pages_count = int((len(notifkeys) / 10)) + (len(notifkeys) % 10 > 0)

        ids = notifkeys[page*10:page*10+10]

        result = {i: notifs[i] for i in ids}
        unseen = {'all': 0}
        for cat in notif_categories:
            unseen[cat] = 0

        for k in notifs:
            current_notif = notifs[k]
            if not current_notif['seen']:
                unseen['all'] += 1
                if unseen.get(current_notif['category']) is None:
                    continue
                unseen[current_notif['category']] += 1

        resp.media = {'result': result, 'unseen': unseen, 'pages': pages_count}

    @auth_guard
    @spectree.validate(json=NotificationsPostReq, resp=Response(HTTP_200=NotificationsPostResp))
    def on_post(self, req, resp):
        self.mark_activity(req)
        n_id = req.media['notification_id']
        username = self.get_username(req).unwrap()

        notifications = self.dbs['notifications'].get(username, {})
        notification = notifications.get(n_id, None)
        if not notification:
            return
        notification['seen'] = True
        self.dbs['notifications'][username] = notifications
        resp.media = {'success': True}

class ReadAllNotifications(Route):
    @auth_guard
    def on_post(self, req, resp):
        self.mark_activity(req)
        category = req.media.get('category', None)
        username = self.get_username(req).unwrap()

        notifications = self.dbs['notifications'][username]

        for nid in notifications:
            if not category or notifications[nid]['category'] == category:
                notifications[nid]['seen'] = True

        self.dbs['notifications'][username] = notifications

        resp.media = {'success': True}
