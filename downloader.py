from datetime import datetime
import os
import pickle
import shutil
import json

import tweepy
import requests
from redis import Redis
import flask
from flask import Flask
from google.oauth2.credentials import Credentials

# Google Drive API
from googleapiclient.discovery import build
import google_auth_oauthlib.flow
# from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.http import MediaFileUpload

SWITCH_HASTAG = "NintendoSwitch"
DRIVE_CREDENTIALS_FILE = 'credentials.json'
DRIVE_CREDENTIALS_PICKLE = 'token.pickle'

SUCCESS_STATUS = 200

SCOPES = [
    'https://www.googleapis.com/auth/drive.metadata.readonly',
    'https://www.googleapis.com/auth/drive.file',
    ]

print("Checking if all required environment variables are set")
mandatory_vars = ['API_KEY', 'API_SECRET_KEY', 'ACCESS_TOKEN', 'ACCESS_TOKEN_SECRET', 'GDRIVE_FOLDER_NAME']
for var in mandatory_vars:
    if var not in os.environ:
        raise EnvironmentError(f"Failed because {var} is not set.")

consumer_key = os.environ.get("API_KEY")
consumer_secret = os.environ.get("API_SECRET_KEY")
access_token = os.environ.get("ACCESS_TOKEN")
access_secret = os.environ.get("ACCESS_TOKEN_SECRET")

screenshots_save_folder = os.environ.get("GDRIVE_FOLDER_NAME")

redis_url = os.environ.get("REDIS_URL", None)
redis_client = None if redis_url is None else Redis.from_url(redis_url)

print("All environment variables are set.")

def get_and_save_gdrive_credentials():
    if not os.path.exists(DRIVE_CREDENTIALS_FILE):
        if 'GDRIVE_CREDENTIALS' not in os.environ:
            raise EnvironmentError(f"Failed to fetch Google Drive credentials because GDRIVE_CREDENTIALS is not set.")
        json = os.environ.get("GDRIVE_CREDENTIALS")
        with open(DRIVE_CREDENTIALS_FILE, 'w') as f:
            f.write(json)
        print("Fetched and saved credentials.json")
    else:
        print("credentials.json is already present.")

print("Checking if Google Drive credentials are set")
get_and_save_gdrive_credentials()

print("Authenticating the Twitter API")
auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
auth.set_access_token(access_token, access_secret)

api = tweepy.API(auth_handler=auth)
print("Twitter API authenticated")

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET")

def save_media(filename, raw_data):
    if not os.path.exists('tmp'):
        os.makedirs('tmp')
    with open(os.path.join('tmp', filename), 'wb') as f:
        shutil.copyfileobj(raw_data, f)

def get_tweet_media(tweet_status, delete_tweet=False):
    '''
    If delete_tweet is true, the tweet WILL be destroyed after
    retrieving the media. Use carefuly.
    '''
    tweet_json = tweet_status._json
    tweet_hashtags = tweet_json['entities']['hashtags']
    result = []
    
    current_time = datetime.now()

    if verify_hastag_in_hashtags(tweet_hashtags):
        game_name = get_game_name(tweet_hashtags)
        tweet_media = tweet_json['extended_entities']['media']
        # there might be more than one photo/video per tweet
        count = 1
        for media in tweet_media:
            if media['type'] == 'video':
                media_url = media['video_info']['variants'][0]['url']
            else:
                media_url = media['media_url_https']
            # if media type is video, download video
            file_extension = media_url.split('/')[-1].split('.')[-1][:3]
            filename = "-".join([game_name, current_time.strftime("%d-%m-%Y-%H%M%S"), str(count)]) + '.' + file_extension
            request = requests.get(media_url, stream=True)
            if request.status_code == SUCCESS_STATUS:
                request.raw.decode_content = True
                save_media(filename, request.raw)
                result.append((filename, request.headers['content-type']))
                count += 1
        if delete_tweet:
            tweet_status.destroy()
    return result

def verify_hastag_in_hashtags(tweet_hashtags):
    for hashtag in tweet_hashtags:
        if hashtag['text'] == SWITCH_HASTAG:
            return True
    return False

def get_game_name(tweet_hashtags):
    name_index = len(tweet_hashtags) - 2
    return tweet_hashtags[name_index]['text']
    
@app.route('/authorize')
def authorize():
    # Create a flow instance to manage the OAuth 2.0 Authorization Grant Flow
    # steps.
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        DRIVE_CREDENTIALS_FILE, scopes=SCOPES)
    flow.redirect_uri = flask.url_for('oauth2callback', _external=True)
    authorization_url, state = flow.authorization_url(
        # This parameter enables offline access which gives your application
        # both an access and refresh token.
        access_type='offline',
        # This parameter enables incremental auth.
        include_granted_scopes='true')

    # Store the state in the session so that the callback can verify that
    # the authorization server response.
    flask.session['state'] = state

    return flask.redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    # Specify the state when creating the flow in the callback so that it can
    # verify the authorization server response.
    state = flask.session['state']
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        DRIVE_CREDENTIALS_FILE, scopes=SCOPES, state=state)
    flow.redirect_uri = flask.url_for('oauth2callback', _external=True)

    # Use the authorization server's response to fetch the OAuth 2.0 tokens.
    authorization_response = flask.request.url
    flow.fetch_token(authorization_response=authorization_response)

    credentials = flow.credentials
    print(f"Credentials refresh_token: {credentials.refresh_token}")
    save_gdrive_credentials(credentials)
    return 'OK'

def save_gdrive_credentials(credentials):
    if redis_client is not None:
        creds_pickle = pickle.dumps(credentials)
        redis_client.mset({'gdrive_credentials': creds_pickle})

    with open('token.pickle', 'wb') as token:
        pickle.dump(credentials, token)

def load_gdrive_credentials():
    result = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            result = pickle.load(token)
    elif redis_client is not None:
        creds_bytes = redis_client.mget('gdrive_credentials')[0]
        result = pickle.loads(creds_bytes)

    return result

def get_gdrive_service():
    creds = load_gdrive_credentials()
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Login required.
            return None
        # Save the credentials for the next run
        save_gdrive_credentials(creds)
    # return Google Drive API service
    return build('drive', 'v3', credentials=creds)

def get_folder_id(gdrive_service):
    page_token = None
    query = f"mimeType='application/vnd.google-apps.folder' and name = '{screenshots_save_folder}'"
    while True:
        response = gdrive_service.files().list(q=query,
                                            spaces='drive',
                                            fields='nextPageToken, files(id, name)',
                                            pageToken=page_token).execute()
        for file in response.get('files', []):
            return file.get('id', None)
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            return None

def create_gdrive_folder(gdrive_service):
    file_metadata = {
        'name': screenshots_save_folder,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    file = gdrive_service.files().create(body=file_metadata,
                                        fields='id').execute()
    return file.get('id')

def upload_files(files):
    print("Uploading the following files: " + str(files))
    # authenticate the user
    gdrive_service = get_gdrive_service()
    if gdrive_service is None:
        print("Failed to authenticate the user in Google Drive!")
        return ['Failed to authenticate user in Google Drive, login required.']

    # check if folder exists, if so, grab the id and continue, else create it
    folder_id = get_folder_id(gdrive_service)
    
    if folder_id is None:
        folder_id = create_gdrive_folder(gdrive_service)

    result = []
    for filename, content_type in files:
        file_metadata = {
            'name': filename,
            'parents': [folder_id],
        }
        media = MediaFileUpload(os.path.join('tmp', filename), mimetype=content_type, resumable=True)
        gdrive_service.files().create(body=file_metadata,
                                    media_body=media,
                                    fields='id').execute()
        result.append(f'Uploaded {filename}')
    return result

def remove_tmp_directory():
    if os.path.exists('tmp'):
        shutil.rmtree('tmp')

@app.route('/', defaults={'delete_tweet': ''}, methods=['POST'])
@app.route('/<string:delete_tweet>', methods=['POST'])
def download_new_tweet_media(delete_tweet):
    delete_tweet = bool(delete_tweet)
    print(f"Received request to download tweet media, delete tweet after deleting? {delete_tweet}")
    tweets = api.user_timeline(count=3)
    files = []
    for t in tweets:
        files += get_tweet_media(t, delete_tweet=delete_tweet)
    print(f"Retrieved the following media: {files}")
    result = upload_files(files)
    print("Files uploaded, cleaning tmp folder.")
    remove_tmp_directory()
    return '\n'.join(result)
