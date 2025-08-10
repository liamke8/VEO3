from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session
import os
import json
import requests
from dotenv import load_dotenv
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
app.config['PEXELS_API_KEY'] = os.environ.get('PEXELS_API_KEY')
app.config['PEXELS_API_URL'] = 'https://api.pexels.com/videos/search'
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

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
        scopes=['https://www.googleapis.com/auth/youtube.upload']
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

def get_upload_status():
    if not os.path.exists('upload_status.json'):
        return {}
    with open('upload_status.json', 'r') as f:
        return json.load(f)

def save_upload_status(data):
    with open('upload_status.json', 'w') as f:
        json.dump(data, f, indent=4)

@app.route('/')
def index():
    credentials = session.get('credentials')
    is_authenticated = True if credentials else False
    video_files = [f for f in os.listdir(app.config['VIDEO_FOLDER']) if f.endswith('.mp4')]
    upload_status = get_upload_status()
    return render_template('index.html', videos=video_files, is_authenticated=is_authenticated, upload_status=upload_status)

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
    videos = fetch_videos(query=query)
    for video in videos:
        video_url = video['video_files'][0]['link']
        video_id = video['id']
        video_filename = f"{video_id}.mp4"
        video_path = os.path.join(app.config['VIDEO_FOLDER'], video_filename)

        if not os.path.exists(video_path):
            with requests.get(video_url, stream=True) as r:
                r.raise_for_status()
                with open(video_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
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

    upload_status = get_upload_status()
    upload_status[filename] = response.get('id')
    save_upload_status(upload_status)

    print(f"Upload successful! Video ID: {response.get('id')}")
    return redirect(url_for('index'))

if __name__ == '__main__':
    if not os.path.exists(app.config['VIDEO_FOLDER']):
        os.makedirs(app.config['VIDEO_FOLDER'])
    app.run(debug=True)
