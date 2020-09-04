import tweepy
import requests

MANDATORY_KEYS = \
    ['API_KEY', 'API_SECRET_KEY', 'ACCESS_TOKEN', 'ACCESS_TOKEN_SECRET', 'BEARER_TOKEN', 'WEBHOOK_ENV_NAME']

class TwitterHandler():

    twitter_api: tweepy.API

    def __init__(self, twitter_params):
        for key in MANDATORY_KEYS:
            if key not in twitter_params:
                raise EnvironmentError(f"Failed because {key} is not set.")

        self.consumer_key = twitter_params['API_KEY']
        self.consumer_secret = twitter_params['API_SECRET_KEY']
        self.access_token = twitter_params['ACCESS_TOKEN']
        self.access_secret = twitter_params['ACCESS_TOKEN_SECRET']
        self.bearer_token = twitter_params['BEARER_TOKEN']
        self.webhook_environment_name = twitter_params['WEBHOOK_ENV_NAME']
        self.authenticate_twitter_api()

    def authenticate_twitter_api(self):
        print("Authenticating the Twitter API")
        auth = tweepy.OAuthHandler(self.consumer_key, self.consumer_secret)
        auth.set_access_token(self.access_token, self.access_secret)
        print("Twitter API authenticated")
        self.twitter_api = tweepy.API(auth_handler=auth)

    def get_user_tweets(self, number_of_tweets=1):
        return self.twitter_api.user_timeline(count=number_of_tweets)