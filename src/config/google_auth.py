from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from src.config.paths import google_client_path, google_token_path

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]



def _load_saved_credentials() -> Credentials | None:
    token_path = google_token_path()
    if not token_path.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except (ValueError, GoogleAuthError):
        # corrupt or written under different scopes -- treat as no token at all
        return None


def _run_consent_flow() -> Credentials:
    client_path = google_client_path()
    if not client_path.exists():
        raise FileNotFoundError(
            f"Google Calendar isn't set up: put your OAuth desktop client at {client_path}"
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(google_client_path()), SCOPES)
    # access_type=offline is what gets us a refresh_token back; prompt=consent forces
    # Google to reissue one even when it thinks we already have a valid grant
    return flow.run_local_server(port=0, access_type="offline", prompt="consent")


def get_calendar_credentials() -> Credentials:
    creds = _load_saved_credentials()

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except GoogleAuthError as error:
            # While the OAuth consent screen is in Testing mode Google expires the
            # refresh token after ~7 days. Previously this raised and the token had to
            # be deleted by hand; now we just fall back to a fresh consent flow.
            print(f"[google_auth] refresh failed ({error}); re-running consent flow")
            creds = _run_consent_flow()
    else:
        creds = _run_consent_flow()

    with open(google_token_path(), "w") as token:
        token.write(creds.to_json())

    return creds
