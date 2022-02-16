
import os
from google_auth_oauthlib.flow import Flow
import google.auth.transport.requests
from google.oauth2 import id_token


class GoogleOATH():
    client_config = {"web": {"client_id": "325044320307-cc3pep0vqfcqlc4dkn5oorok1f0a56qb.apps.googleusercontent.com",
                             "project_id": "warm-particle-274811",
                             "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                             "token_uri": "https://oauth2.googleapis.com/token",
                             "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                             "client_secret": "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",
                             "redirect_uris": ["https://beta.obtrader.ml/api/v1/login_with_google"]}
                     }

    flow = Flow.from_client_config(
        client_config=client_config,
        scopes=["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "openid"],
        redirect_uri=f"{client_config['web']['redirect_uris'][-1]}"
    )

    GOOGLE_CLIENT_ID = '325044320307-cc3pep0vqfcqlc4dkn5oorok1f0a56qb.apps.googleusercontent.com'

    def __init__(self) -> None:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    def callback(self, requests):
        self.flow.fetch_token(authorization_response=requests.url)
        credentials = self.flow.credentials
        token_request = google.auth.transport.requests.Request()
        id_info = id_token.verify_oauth2_token(
            id_token=credentials._id_token,
            request=token_request,
            audience=self.GOOGLE_CLIENT_ID
        )
        return id_info


if __name__ == '__main__':
    pass
