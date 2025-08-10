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

# Configuration
app.config['VIDEO_FOLDER'] = 'videos'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PEXELS_API_KEY'] = os.environ.get('PEXELS_API_KEY')
app.config['PEXELS_API_URL'] = 'https://api.pexels.com/videos/search'
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

db = SQLAlchemy(app)

# Database Model
class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), unique=True, nullable=False)
    pexels_id = db.Column(db.String(200), nullable=True)
    youtube_video_id = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(50), nullable=False, default='downloaded') # e.g., downloaded, uploaded

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

def fetch_videos(query='nature', per_page=5):
    headers = {
        'Authorization': app.config['PEXELS_API_KEY']
    }
    params = {
        'query': query,
        'per_page': per_page
    }
    response = requests.get(app.config['PEXELS_API_URL'], headers=headers, params=params)
    if response.status_code == 200:
        return response.json().get('videos', [])
    return []

@app.route('/')
def index():
    credentials = session.get('credentials')
    is_authenticated = True if credentials else False
    videos = Video.query.all()
    return render_template('index.html', videos=videos, is_authenticated=is_authenticated)

@app.route('/authorize')
def authorize():
    flow = get_google_auth_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session['state']
    flow = get_google_auth_flow()
    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('credentials', None)
    return redirect(url_for('index'))

@app.route('/fetch_videos', methods=['POST'])
def fetch_videos_route():
    query = request.form.get('query', 'nature')
    videos_data = fetch_videos(query=query)
    for video_data in videos_data:
        video_id = video_data['id']
        video_filename = f"{video_id}.mp4"

        # Check if video already exists in DB
        existing_video = Video.query.filter_by(filename=video_filename).first()
        if existing_video:
            continue

        video_url = video_data['video_files'][0]['link']
        video_path = os.path.join(app.config['VIDEO_FOLDER'], video_filename)

        if not os.path.exists(video_path):
            with requests.get(video_url, stream=True) as r:
                r.raise_for_status()
                with open(video_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

        new_video = Video(filename=video_filename, pexels_id=str(video_id))
        db.session.add(new_video)

    db.session.commit()
    return redirect(url_for('index'))

@app.route('/videos/<filename>')
def video_file(filename):
    return send_from_directory(app.config['VIDEO_FOLDER'], filename)

@app.route('/upload/<filename>')
def upload_form(filename):
    if 'credentials' not in session:
        return redirect(url_for('authorize'))
    return render_template('upload.html', filename=filename)

@app.route('/upload_video/<filename>', methods=['POST'])
def upload_video(filename):
    if 'credentials' not in session:
        return redirect(url_for('authorize'))

    video = Video.query.filter_by(filename=filename).first_or_404()

    credentials = Credentials(**session['credentials'])
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
    if 'credentials' not in session:
        return redirect(url_for('authorize'))

    credentials = Credentials(**session['credentials'])

    uploaded_videos = Video.query.filter(Video.youtube_video_id.isnot(None)).all()
    video_ids = [video.youtube_video_id for video in uploaded_videos]

    analytics_data = {}
    if video_ids:
        analytics_data = get_youtube_analytics(credentials, video_ids)

    return render_template('dashboard.html', analytics_data=analytics_data)


if __name__ == '__main__':
    if not os.path.exists(app.config['VIDEO_FOLDER']):
        os.makedirs(app.config['VIDEO_FOLDER'])

    with app.app_context():
        db.create_all()
        migrate_json_to_db()

    app.run(debug=True)
