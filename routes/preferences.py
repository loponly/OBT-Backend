from routes.utility.notify_alert import NotifyMailPreferences
import falcon
from spectree import Response
from pydantic import BaseModel
from typing import List, Dict, Any
from typing_extensions import Literal

from .base import Route, StandardResponse, auth_guard
from .spectree import spectree
from .notifications import notif_categories


class NotificationPreferencesGetResp(BaseModel):
    result: List[str]


class NotificationPreferencesPostReq(BaseModel):
    category: str


class NotificationPreferencesPostResp(BaseModel):
    success: bool = True


class NotificationPreferencesPostMessage(BaseModel):
    message: str


class NotificationPreferencesDeleteReq(BaseModel):
    category: str


class NotificationPreferencesDeleteResp(BaseModel):
    success: bool = True


class MailingPreferencesGetResp(BaseModel):
    options: Dict[str, Any]
    current: Any


PerformanceReportOptions = ('none', 'weekly', 'monthly')
PerformanceReportType = Literal[PerformanceReportOptions]


class MailingPreferencesPostReq(BaseModel):
    performance_report: PerformanceReportType


class NotificationPreferences(Route):
    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=NotificationPreferencesGetResp))
    def on_get(self, req, resp):
        self.mark_activity(req)
        profile = self.get_profile(req).unwrap()
        prefs = profile.get('preferences', {}).get('notif_categories', [])
        resp.media = {'result': prefs}

    @auth_guard
    @spectree.validate(json=NotificationPreferencesPostReq, resp=Response(HTTP_200=NotificationPreferencesPostResp, HTTP_400=NotificationPreferencesPostMessage))
    def on_post(self, req, resp):
        self.mark_activity(req)
        category = req.media['category']
        if category not in notif_categories:
            resp.media = {'message': 'Category not found'}
            resp.status = falcon.HTTP_400
            return

        username = self.get_username(req).unwrap()
        profile = self.get_profile(req).unwrap()

        # compatibility with older accounts
        if not profile.get('preferences', None):
            profile['preferences'] = {'notif_categories': []}
            self.dbs['users'][username] = profile

        if category not in profile['preferences']['notif_categories']:
            profile['preferences']['notif_categories'].append(category)
            self.dbs['users'][username] = profile
        resp.media = {'success': True}

    @auth_guard
    @spectree.validate(json=NotificationPreferencesDeleteReq, resp=Response(HTTP_200=NotificationPreferencesDeleteResp))
    def on_delete(self, req, resp):
        self.mark_activity(req)
        category = req.media['category']
        username = self.get_username(req).unwrap()
        profile = self.get_profile(req).unwrap()

        prefs = profile.get('preferences', {}).get('notif_categories', [])

        if category in prefs:
            profile['preferences']['notif_categories'].remove(category)
            self.dbs['users'][username] = profile
        resp.media = {'success': True}


class MailingPreferences(Route):
    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=MailingPreferencesGetResp))
    def on_get(self, req, resp):
        username = self.get_username(req).unwrap()
        user = self.dbs['users'][username]
        current = user['preferences'].get('mail', {'performance_report': PerformanceReportOptions[0]})
        for k in NotifyMailPreferences.notify_categories:
            if not current.get(k, False):
                current[k] = False
        resp.media = {'options': {'performance_report': list(PerformanceReportOptions)}, 'current': current}

    @auth_guard
    @spectree.validate(json=MailingPreferencesPostReq, resp=Response(HTTP_200=StandardResponse))
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        # TODO: validate options
        user = self.dbs['users'][username]

        if not user.get('preferences', None):
            user['preferences'] = {'notif_categories': []}

        user['preferences'] = user.get('preferences', {})
        user['preferences']['mail'] = user['preferences'].get('mail', {})
        user['preferences']['mail'].update(req.media)
        self.dbs['users'][username] = user
        resp.media = {'success': True}
