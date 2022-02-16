import time
import uuid
from typing import NamedTuple


class Notification(NamedTuple):
    title: str
    text: str
    category: str
    timestamp: int
    icon: str = None
    glob: bool = False
    link: str = None
    seen: bool = False


class NotificationSaving():
    def __init__(self, dbs: dict):
        self.dbs = dbs

    def save_notification(self, notification: Notification, username):
        if notification.category in self.dbs['users'][username].get('preferences', {}).get('notif_categories', []):
            return

        notifications = self.dbs['notifications'].get(username, {})
        newest = sorted(notifications.keys(), key=lambda k: notifications[k]['timestamp'], reverse=True)
        newest = newest[:200]
        notifications = {k: notifications[k] for k in newest}
        notifications[str(uuid.uuid4())] = notification._asdict()
        self.dbs['notifications'][username] = notifications

    def save_global_notification(self, notification: Notification):
        users = self.dbs['users']
        for u in users:
            if notification.category in self.dbs['users'][u].get('preferences', {}).get('notif_categories', []):
                continue

            notifications = self.dbs['notifications'].get(u, {})
            newest = sorted(notifications.keys(), key=lambda k: notifications[k]['timestamp'], reverse=True)
            newest = newest[:200]
            notifications = {k: notifications[k] for k in newest}
            notifications[str(uuid.uuid4())] = notification._asdict()
            self.dbs['notifications'][u] = notifications

    def send_notification(self):
        # TODO: Web socket stuff
        raise NotImplementedError("Web socket not implemented")
