#!/usr/bin/env python3
import os, json, time, urllib.request, urllib.parse, urllib.error, threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import iran_api

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

LEAGUES = {
    'nba': 'nba',
    'wnba': 'wnba',
    'ncaa': 'mens-college-basketball'
}

cache = {}
lock = threading.Lock()
chat_lock = threading.Lock()
chat_calls = []


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'CourtSide-Danial/1.0',
            'Accept': 'application/json'
        }
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


class Handler(SimpleHTTPRequestHandler):

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url_parts = urllib.parse.urlparse(self.path)

        if url_parts.path == '/api/config':
            return self.send_json({
                'chatConfigured': bool(os.getenv('OPENAI_API_KEY'))
            })

        if url_parts.path in ('/api/scores', '/api/standings'):
            query = urllib.parse.parse_qs(url_parts.query)
            league = query.get('league', ['nba'])[0]
            date = query.get('date', [''])[0]

            if league not in LEAGUES and league != 'iran':
                return self.send_json(
                    {'error': 'لیگ پشتیبانی نمی‌شود'}, 400
                )

            if date and (len(date) != 8 or not date.isdigit()):
                return self.send_json(
                    {'error': 'تاریخ نامعتبر است'}, 400
                )

            if league == 'iran':
                try:
                    if url_parts.path.endswith('scores'):
                        fetched_at, data = iran_api.scores(date)
                    else:
                        fetched_at, data = iran_api.standings()

                    return self.send_json({
                        'data': data,
                        'fetchedAt': int(fetched_at * 1000),
                        'source': 'Highlightly',
                        'refreshMinutes': 30
                    })
                except iran_api.IranError as error:
                    return self.send_json({'error': str(error)}, 503)

            if url_parts.path.endswith('scores'):
                kind = 'scoreboard'
                base = 'https://site.api.espn.com/apis/site/v2/sports/basketball/'
            else:
                kind = 'standings'
                base = 'https://site.api.espn.com/apis/v2/sports/basketball/'

            url = base + LEAGUES[league] + '/' + kind

            if kind == 'scoreboard' and date:
                url += '?dates=' + date + '&limit=100'

            try:
                with lock:
                    item = cache.get(url)

                if item and time.time() - item[0] < 15:
                    fetched_at, data = item
                else:
                    data = fetch(url)
                    fetched_at = time.time()
                    with lock:
                        cache[url] = (fetched_at, data)

                return self.send_json({
                    'data': data,
                    'fetchedAt': int(fetched_at * 1000),
                    'source': 'ESPN (unofficial)'
                })

            except Exception:
                return self.send_json({
                    'error': 'دریافت اطلاعات از منبع ممکن نشد.'
                }, 502)

        return super().do_GET()

    def do_POST(self):
        if self.path != '/api/chat':
            return self.send_json({'error': 'Not found'}, 404)

        key = os.getenv('OPENAI_API_KEY')

        if not key:
            return self.send_json({
                'error': 'گفت‌وگوی هوش مصنوعی هنوز فعال نشده است.'
            }, 503)

        try:
            length = int(self.headers.get('Content-Length', '0'))

            if length > 20000:
                return self.send_json({
                    'error': 'پیام بیش از حد طولانی است.'
                }, 413)

            body = json.loads(self.rfile.read(length))
            message = str(body.get('message', '')).strip()

            if not message or len(message) > 2000:
                return self.send_json({
                    'error': 'پیام نامعتبر است.'
                }, 400)

            with chat_lock:
                now = time.time()
                chat_calls[:] = [
                    t for t in chat_calls if now - t < 86400
                ]

                hourly_count = sum(
                    1 for t in chat_calls if now - t < 3600
                )

                if len(chat_calls) >= 20 or hourly_count >= 8:
                    return self.send_json({
                        'error': 'ظرفیت گفتگو تکمیل شده است.'
                    }, 429)

                chat_calls.append(now)

            payload = {
                'model': os.getenv(
                    'OPENAI_MODEL',
                    'llama-3.1-8b-instant'
                ),
                'messages': [
                    {
                        'role': 'system',
                        'content': (
                            'You are CourtSide assistant for a Persian '
                            'basketball website by Danial Nayebi. '
                            'Answer in Persian concisely and accurately '
                            'about basketball players, history and video. '
                            'Do not claim access to real-time information. '
                            'Clearly say when uncertain. For video '
                            'suggestions, suggest search terms and do not '
                            'invent links.'
                        )
                    },
                    {
                        'role': 'user',
                        'content': message
                    }
                ],
                'max_tokens': 350
            }

            request = urllib.request.Request(
                'https://api.groq.com/openai/v1/chat/completions',
                data=json.dumps(payload).encode(),
                headers={
                    'Authorization': 'Bearer ' + key,
                    'Content-Type': 'application/json'
                }
            )

            with urllib.request.urlopen(request, timeout=35) as response:
                data = json.load(response)

            return self.send_json({
                'reply': data['choices'][0]['message']['content']
            })

        except Exception:
            return self.send_json({
                'error': 'ارتباط با سرویس هوش مصنوعی برقرار نشد.'
            }, 502)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    print(f'CourtSide running on {port}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
