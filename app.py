from flask import Flask, render_template, request, redirect, url_for, send_from_directory
import os
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Configuration
app.config['VIDEO_FOLDER'] = 'videos'
app.config['PEXELS_API_KEY'] = os.environ.get('PEXELS_API_KEY')
app.config['PEXELS_API_URL'] = 'https://api.pexels.com/videos/search'

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
    video_files = [f for f in os.listdir(app.config['VIDEO_FOLDER']) if f.endswith('.mp4')]
    return render_template('index.html', videos=video_files)

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

if __name__ == '__main__':
    if not os.path.exists(app.config['VIDEO_FOLDER']):
        os.makedirs(app.config['VIDEO_FOLDER'])
    app.run(debug=True)
