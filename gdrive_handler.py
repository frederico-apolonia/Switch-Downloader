import pickle
import os

import requests
import google_auth_oauthlib.flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build, Resource
from google.auth.transport.requests import Request
from googleapiclient.http import MediaFileUpload

DRIVE_CREDENTIALS_FILE = 'gdrive_credentials.json'
DRIVE_CREDENTIALS_PICKLE = 'gdrive_token.pickle'
REDIS_DRIVE_CREDENTIALS_KEY = 'gdrive_credentials'

SCOPES = [
    'https://www.googleapis.com/auth/drive.metadata.readonly',
    'https://www.googleapis.com/auth/drive.file',
    ]

MANDATORY_KEYS = \
    ['CREDENTIALS', 'REDIS', 'FOLDER_NAME']

class GDriveHandler():

    gdrive_service: Resource

    def __init__(self, gdrive_params):
        for key in MANDATORY_KEYS:
            if key not in gdrive_params:
                raise EnvironmentError(f"Failed because {key} is not set.")

        self.credentials_json = gdrive_params['CREDENTIALS_JSON']
        self.folder_name = gdrive_params['FOLDER_NAME']
        self.redis_client = gdrive_params['REDIS']

        self.persist_gdrive_json_credentials()

    def persist_gdrive_json_credentials(self):
        if not os.path.exists(DRIVE_CREDENTIALS_FILE):
            json = self.credentials_json
            with open(DRIVE_CREDENTIALS_FILE, 'w') as f:
                f.write(json)
            print(f'Persisted GDrive credentials.')

    def save_gdrive_credentials(self, credentials):
        if self.redis_client is not None:
            creds_pickle = pickle.dumps(credentials)
            self.redis_client.mset({REDIS_DRIVE_CREDENTIALS_KEY: creds_pickle})

        with open(DRIVE_CREDENTIALS_PICKLE, 'wb') as token:
            pickle.dump(credentials, token)

    def load_gdrive_credentials(self):
        result = None
        if os.path.exists(DRIVE_CREDENTIALS_PICKLE):
            with open(DRIVE_CREDENTIALS_PICKLE, 'rb') as token:
                result = pickle.load(token)
        elif self.redis_client is not None:
            creds_bytes = self.redis_client.mget(REDIS_DRIVE_CREDENTIALS_KEY)[0]
            result = pickle.loads(creds_bytes)
        return result

    def set_gdrive_service(self):
        credentials = self.load_gdrive_credentials()
        # If there are no (valid) credentials available, let the user log in.
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                # Login required.
                return False
            # Save the credentials for the next run
            self.save_gdrive_credentials(credentials)
        # return Google Drive API service
        self.gdrive_service = build('drive', 'v3', credentials=credentials)
        return self.gdrive_service is not None

    def get_screenshots_folder_id(self):
        page_token = None
        query = f"mimeType='application/vnd.google-apps.folder' and name = '{self.folder_name}'"
        while True:
            response = self.gdrive_service.files().list(q=query,
                                                        spaces='drive',
                                                        fields='nextPageToken, files(id, name)',
                                                        pageToken=page_token).execute()
            for file in response.get('files', []):
                return file.get('id', None)
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                return None

    def create_screenshots_folder(self):
        file_metadata = {
            'name': self.folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        file = self.gdrive_service.files().create(body=file_metadata,
                                                  fields='id').execute()
        return file.get('id')

    def upload_file_to_screenshot_folder(self, filepath, file_metadata, content_type):
        media = MediaFileUpload(filepath, mimetype=content_type, resumable=True)
        self.gdrive_service.files().create(body=file_metadata,
                                        media_body=media,
                                        fields='id').execute()

    def fetch_and_save_credentials(self, state, redirect_uri, authorization_response):
        flow = create_flow_instance(redirect_uri, state)
        flow.fetch_token(authorization_response=authorization_response)
        
        self.save_gdrive_credentials(flow.credentials)


def create_flow_instance(redirect_uri, state=None):
    result = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        DRIVE_CREDENTIALS_FILE, scopes=SCOPES, state=state)
    result.redirect_uri = redirect_uri
    return result

def create_authorization_redirect(redirect_uri):
    flow = create_flow_instance(redirect_uri)
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
    )
    return authorization_url, state