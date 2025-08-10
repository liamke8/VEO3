from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session
import os
import json
import requests
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey")

import shutil
import uuid

# Configuration
app.config['VIDEO_FOLDER'] = 'videos'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# app.config['PEXELS_API_KEY'] = os.environ.get('PEXELS_API_KEY') # No longer needed
# app.config['PEXELS_API_URL'] = 'https://api.pexels.com/videos/search' # No longer needed
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')
app.config['VEO_API_KEY'] = os.environ.get('VEO_API_KEY') # Placeholder for Veo API Key

db = SQLAlchemy(app)

# Database Models
class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    credentials_json = db.Column(db.Text, nullable=False)
    videos = db.relationship('Video', backref='account', lazy=True)

    def __repr__(self):
        return f"Account('{self.name}')"

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), unique=True, nullable=False)
    generation_prompt = db.Column(db.Text, nullable=True)
    youtube_video_id = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(50), nullable=False, default='generated') # e.g., generated, uploaded
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=True) # Can be nullable if we have videos not associated with an account

    def __repr__(self):
        return f"Video('{self.filename}', '{self.status}')"

# This allows us to use a non-HTTPS redirect URI for local development.
# In production, this should be removed and the app should use HTTPS.
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

def get_google_auth_flow():
    client_secrets_file = {
        "web": {
            "client_id": app.config['GOOGLE_CLIENT_ID'],
            "client_secret": app.config['GOOGLE_CLIENT_SECRET'],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [url_for('oauth2callback', _external=True)]
        }
    }
    flow = google_auth_oauthlib.flow.Flow.from_client_config(
        client_secrets_file,
        scopes=[
            'https://www.googleapis.com/auth/youtube.upload',
            'https://www.googleapis.com/auth/youtube.readonly'
        ]
    )
    flow.redirect_uri = url_for('oauth2callback', _external=True)
    return flow

def generate_video_from_prompt(prompt):
    """
    Placeholder function for AI video generation.
    In a real implementation, this would call the Google Veo API.
    For now, it just copies a sample video to a new unique filename.
    """
    print(f"Generating video for prompt: '{prompt}'...")
    # Simulate API call delay
    import time
    time.sleep(3)

    source_video_path = 'assets/sample_video.mp4'
    new_filename = f"generated_{uuid.uuid4()}.mp4"
    destination_path = os.path.join(app.config['VIDEO_FOLDER'], new_filename)

    shutil.copy(source_video_path, destination_path)

    print(f"Video generated and saved to {destination_path}")
    return new_filename


@app.route('/')
def index():
    accounts = Account.query.all()
    videos = Video.query.all()
    return render_template('index.html', videos=videos, accounts=accounts)

@app.route('/authorize')
def authorize():
    flow = get_google_auth_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session.pop('state', None)
    if not state or state != request.args.get('state'):
        return 'State mismatch. Please try again.', 400

    flow = get_google_auth_flow()
    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials

    # Get user's channel info to use as account name
    youtube = googleapiclient.discovery.build(
        'youtube', 'v3', credentials=credentials)
    response = youtube.channels().list(part='snippet', mine=True).execute()
    channel_title = response['items'][0]['snippet']['title']
    account_name = channel_title or 'Unnamed Account'

    # Store credentials in the database
    new_account = Account(
        name=account_name,
        credentials_json=credentials.to_json()
    )
    db.session.add(new_account)
    db.session.commit()

    return redirect(url_for('index'))

@app.route('/delete_account/<int:account_id>', methods=['POST'])
def delete_account(account_id):
    account = Account.query.get_or_404(account_id)
    # Optional: Also delete associated videos or handle them as needed
    # Video.query.filter_by(account_id=account.id).delete()
    db.session.delete(account)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/ideation')
def ideation():
    return render_template('ideation.html')

@app.route('/generate_video', methods=['POST'])
def generate_video():
    prompt = request.form.get('prompt')
    if not prompt:
        return "No prompt provided.", 400

    new_filename = generate_video_from_prompt(prompt)

    new_video = Video(
        filename=new_filename,
        generation_prompt=prompt
    )
    db.session.add(new_video)
    db.session.commit()

    return redirect(url_for('index'))

@app.route('/videos/<filename>')
def video_file(filename):
    return send_from_directory(app.config['VIDEO_FOLDER'], filename)

@app.route('/upload/<filename>')
def upload_form(filename):
    accounts = Account.query.all()
    if not accounts:
        return redirect(url_for('authorize'))
    return render_template('upload.html', filename=filename, accounts=accounts)

@app.route('/upload_video/<filename>', methods=['POST'])
def upload_video(filename):
    account_id = request.form.get('account_id')
    if not account_id:
        return "No account selected.", 400

    account = Account.query.get_or_404(account_id)
    video = Video.query.filter_by(filename=filename).first_or_404()

    credentials = Credentials.from_authorized_user_info(json.loads(account.credentials_json))

    youtube = googleapiclient.discovery.build(
        'youtube', 'v3', credentials=credentials)

    video_path = os.path.join(app.config['VIDEO_FOLDER'], filename)

    body = {
        'snippet': {
            'title': request.form['title'],
            'description': request.form['description'],
            'tags': request.form['tags'].split(','),
            'categoryId': '22' # People & Blogs
        },
        'status': {
            'privacyStatus': request.form['privacyStatus']
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)

    request_body = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        status, response = request_body.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%.")

    video.youtube_video_id = response.get('id')
    video.status = 'uploaded'
    video.account_id = account.id
    db.session.commit()

    print(f"Upload successful! Video ID: {video.youtube_video_id}")
    return redirect(url_for('index'))

def migrate_json_to_db():
    if not os.path.exists('upload_status.json'):
        return

    with open('upload_status.json', 'r') as f:
        upload_status = json.load(f)

    for filename, youtube_id in upload_status.items():
        video = Video.query.filter_by(filename=filename).first()
        if video:
            video.youtube_video_id = youtube_id
            video.status = 'uploaded'
        else:
            # This case should ideally not happen if the app is used normally
            new_video = Video(filename=filename, youtube_video_id=youtube_id, status='uploaded')
            db.session.add(new_video)

    db.session.commit()
    os.rename('upload_status.json', 'upload_status.json.migrated')
    print("Successfully migrated data from upload_status.json to the database.")


def get_youtube_analytics(credentials, video_ids):
    youtube = googleapiclient.discovery.build(
        'youtube', 'v3', credentials=credentials)
    youtube_analytics = googleapiclient.discovery.build(
        'youtubeAnalytics', 'v1', credentials=credentials)

    analytics_data = {}

    # Get video titles from Data API
    video_details = youtube.videos().list(
        part='snippet',
        id=','.join(video_ids)
    ).execute()

    for item in video_details.get('items', []):
        analytics_data[item['id']] = {'title': item['snippet']['title']}

    # Get stats from Analytics API
    # Note: The YouTube Analytics API provides data with a delay of ~48 hours.
    # We will fetch lifetime stats. A proper start date would be the video's publish date.
    analytics_response = youtube_analytics.reports().query(
        ids='channel==MINE',
        startDate='2005-02-14', # YouTube's launch date
        endDate='2100-01-01',
        metrics='views,likes,comments',
        dimensions='video',
        filters=f"video=={','.join(video_ids)}"
    ).execute()

    for row in analytics_response.get('rows', []):
        video_id, views, likes, comments = row
        if video_id in analytics_data:
            analytics_data[video_id]['views'] = views
            analytics_data[video_id]['likes'] = likes
            analytics_data[video_id]['comments'] = comments

    return analytics_data


@app.route('/dashboard')
def dashboard():
    accounts = Account.query.all()
    if not accounts:
        return render_template('dashboard.html', accounts=accounts, analytics_data=None)
    # Default to showing the first account's dashboard
    return redirect(url_for('dashboard_for_account', account_id=accounts[0].id))

@app.route('/dashboard/<int:account_id>')
def dashboard_for_account(account_id):
    accounts = Account.query.all()
    target_account = Account.query.get_or_404(account_id)
    credentials = Credentials.from_authorized_user_info(json.loads(target_account.credentials_json))

    uploaded_videos = Video.query.filter_by(account_id=target_account.id).filter(Video.youtube_video_id.isnot(None)).all()
    video_ids = [video.youtube_video_id for video in uploaded_videos]

    analytics_data = {}
    if video_ids:
        try:
            analytics_data = get_youtube_analytics(credentials, video_ids)
        except Exception as e:
            # Handle cases where token might be expired or invalid
            print(f"Error fetching analytics for account {target_account.name}: {e}")
            return f"Error fetching analytics for account {target_account.name}. Please try reconnecting the account.", 500


    return render_template('dashboard.html', analytics_data=analytics_data, accounts=accounts, current_account_id=target_account.id)


if __name__ == '__main__':
    if not os.path.exists(app.config['VIDEO_FOLDER']):
        os.makedirs(app.config['VIDEO_FOLDER'])

    with app.app_context():
        db.create_all()
        migrate_json_to_db()

    app.run(debug=True)
