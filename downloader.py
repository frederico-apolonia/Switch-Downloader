from datetime import datetime
import os
import pickle
import shutil
import json

import requests
from redis import Redis
import flask
from flask import Flask
from twitter_handler import TwitterHandler
from gdrive_handler import GDriveHandler, create_authorization_redirect

SWITCH_HASTAG = "NintendoSwitch"
TMP_FOLDER = 'tmp'

print("Checking if all required environment variables are set")
mandatory_vars = ['TWITTER_API_KEY', 'TWITTER_API_SECRET_KEY', 'TWITTER_ACCESS_TOKEN', 'TWITTER_ACCESS_TOKEN_SECRET', 'TWITTER_BEARER_TOKEN', 'TWITTER_ENV_NAME', 'GDRIVE_FOLDER_NAME', 'GDRIVE_CREDENTIALS']
for var in mandatory_vars:
    if var not in os.environ:
        raise EnvironmentError(f"Failed because {var} is not set.")

screenshots_save_folder = os.environ.get("GDRIVE_FOLDER_NAME")

redis_url = os.environ.get("REDIS_URL", None)
redis_client = None if redis_url is None else Redis.from_url(redis_url)

print("All environment variables are set.")

print("Authenticating the Twitter API")
twitter_handler_params = {
    'CONSUMER_KEY': os.environ.get('TWITTER_API_KEY'),
    'CONSUMER_SECRET': os.environ.get('TWITTER_API_SECRET_KEY'),
    'ACCESS_TOKEN': os.environ.get('TWITTER_ACCESS_TOKEN'),
    'ACCESS_SECRET': os.environ.get('TWITTER_ACCESS_TOKEN_SECRET'),
    'BEARER_TOKEN': os.environ.get('TWITTER_BEARER_TOKEN'),
    'WEBHOOK_ENV_NAME': os.environ.get('TWITTER_ENV_NAME'),
}
twitter_handler = TwitterHandler(twitter_handler_params)
print("Twitter API authenticated")

gdrive_params = {
    'CREDENTIALS': os.environ.get('GDRIVE_CREDENTIALS'),
    'FOLDER_NAME': os.environ.get('GDRIVE_FOLDER_NAME'),
    'REDIS': redis_client
}
gdrive_handler = GDriveHandler(gdrive_params)

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET")

def save_media(filename, raw_data):
    if not os.path.exists(TMP_FOLDER):
        os.makedirs(TMP_FOLDER)
    with open(os.path.join(TMP_FOLDER, filename), 'wb') as f:
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
            if request.ok:
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
    redirect_uri = flask.url_for('oauth2callback', _external=True)
    authorization_url, state = create_authorization_redirect(redirect_uri)
    flask.session['state'] = state
    return flask.redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    # Specify the state when creating the flow in the callback so that it can
    # verify the authorization server response.
    state = flask.session['state']
    redirect_uri = flask.url_for('oauth2callback', _external=True)
    authorization_response = flask.request.url

    gdrive_handler.fetch_and_save_credentials(state, redirect_uri, authorization_response)
    return 'OK'

def upload_files(files):
    print("Uploading the following files: " + str(files))
    # authenticate the user
    if not gdrive_handler.set_gdrive_service():
        print("Failed to authenticate the user in Google Drive!")
        return ['Failed to authenticate user in Google Drive, login required.']

    # check if folder exists, if so, grab the id and continue, else create it
    folder_id = gdrive_handler.get_screenshots_folder_id()
    if folder_id is None:
        folder_id = gdrive_handler.create_screenshots_folder()

    result = []
    for filename, content_type in files:
        file_metadata = {
            'name': filename,
            'parents': [folder_id],
        }
        filepath = os.path.join(TMP_FOLDER, filename)
        gdrive_handler.upload_file_to_screenshot_folder(filepath, file_metadata, content_type)
        result.append(f'Uploaded {filename}')
    return result

def remove_tmp_directory():
    if os.path.exists(TMP_FOLDER):
        shutil.rmtree(TMP_FOLDER)

@app.route('/webhook/twitter', methods=['POST'])
def twitter_webhook_handler():
    print("Hello!! Recieved a tweet post notification!!")
    pass

@app.route('/', defaults={'delete_tweet': ''}, methods=['POST'])
@app.route('/<string:delete_tweet>', methods=['POST'])
def download_new_tweet_media(delete_tweet):
    delete_tweet = bool(delete_tweet)
    print(f"Received request to download tweet media, delete tweet after deleting? {delete_tweet}")
    tweets = twitter_handler.get_user_tweets(number_of_tweets=3)
    files = []
    for t in tweets:
        files += get_tweet_media(t, delete_tweet=delete_tweet)
    print(f"Retrieved the following media: {files}")
    result = upload_files(files)
    print("Files uploaded, cleaning tmp folder.")
    remove_tmp_directory()
    return '\n'.join(result)
