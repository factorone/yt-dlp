import re
from datetime import datetime
from .common import InfoExtractor
from ..utils import ExtractorError, urlencode_postdata
import json

class FloMarchingLiveIE(InfoExtractor):
    _VALID_URL = r'https://www.flomarching.com/live/(?P<id>\d+)'
    _TESTS = [
        {
            'url': 'https://www.flomarching.com/live/12345',
            'only_matching': True,
        }
    ]

    def _login(self):
        # FloMarching does not support password login; require cookies
        if not self._get_cookies('https://www.flomarching.com'):
            raise ExtractorError(
                'Login with password is not supported for this website. '
                'Use --cookies-from-browser or --cookies for authentication. '
                'See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp for details.'
            )

    def _real_extract(self, url):
        video_id = self._match_id(url)
        self._login()

        # Download the webpage to extract the event_id
        webpage = self._download_webpage(url, video_id)

        # Try to extract stream_list from flo-app-state script first
        stream_list = []
        m_script = re.search(
            r'<script[^>]+id="flo-app-state"[^>]*>(.*?)</script>',
            webpage, re.DOTALL)
        if m_script:
            script_content = m_script.group(1)
            # Replace &q; with "
            script_content = script_content.replace('&q;', '"')
            # Find JSON portion (assume it's the whole content)
            try:
                state_json = json.loads(script_content)
                stream_list = state_json.get('stream_list') or []
            except Exception:
                pass

        # If stream_list is still empty, try the API endpoint
        if not stream_list:
            event_id = video_id
            api_url = (
                f'https://api.flomarching.com/api/experiences/web/legacy-core/live-events/{event_id}?site_id=27&version=1.33.2'
            )
            event_json = self._download_json(api_url, event_id, note='Downloading event metadata')
            data = event_json.get('data', {})
            stream_list = data.get('stream_list') or []
        selected_stream_id = None
        if stream_list:
            # To select a specific stream, pass extractor_args={'flomarching': {'stream': <stream_code_or_id_or_name>}}
            # or extractor_args={'flomarchinglive': {'stream': <stream_code_or_id_or_name>}}
            # Parse extractor-args string for stream selection
            extractor_args_str = self.get_param('extractor-args', '') or ''
            user_stream = None
            if extractor_args_str:
                # Format: "key:value;key2:value2"
                for pair in extractor_args_str.split(';'):
                    if ':' in pair:
                        k, v = pair.split(':', 1)
                        if k.strip() == 'stream':
                            user_stream = v.strip()
                            break
            # Build a map of code, id, and name to stream
            stream_map = {}
            for stream in stream_list:
                stream_map[str(stream.get('stream_id'))] = stream
                stream_map[stream.get('stream_code')] = stream
                stream_map[stream.get('stream_name')] = stream
            if user_stream and user_stream in stream_map:
                selected_stream_id = stream_map[user_stream].get('stream_id')
            else:
                # Prefer active stream, else first
                active = [s for s in stream_list if s.get('stream_active')]
                selected_stream_id = (active[0].get('stream_id') if active else stream_list[0].get('stream_id'))
            # Warn if multiple streams exist and no explicit selection is made
            if len(stream_list) > 1 and not user_stream:
                self.report_warning(
                    f"Multiple streams available: {[s.get('stream_name') for s in stream_list]}. "
                    "Specify --extractor-args \"stream: <stream_name>\" to select a stream."
                )

        # stream id is the one selected
        stream_id = selected_stream_id

        if not stream_id:
            raise ExtractorError('Unable to find stream_id for this event')

        api_url = f'https://live-api-3.flosports.tv/streams/{stream_id}/tokens'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Content-Type': 'application/json',
            'Origin': 'https://www.flomarching.com',
            'DNT': '1',
            'Sec-GPC': '1',
            'Connection': 'keep-alive',
            'Referer': 'https://www.flomarching.com/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache',
            'TE': 'trailers',
            'X-301-Location': 'web',
            'x-flo-app': 'flosports-webapp',
        }
        for cookie in self.cookiejar:
            if cookie.name == 'jwt_token':
                token = cookie.value
                break
        if token:
            headers['Authorization'] = f'Bearer {token}'
        data = json.dumps({"adTracking": {"appName": "flosports-web"}}).encode('utf-8')
        from yt_dlp.networking.common import Request
        request = Request(api_url, data=data, headers=headers)
        request.get_method = 'POST'  # Ensure the request method is POST

        response = self._download_json(
            request, video_id, note='Requesting stream token',
            expected_status=200)

        uri = response.get('data', {}).get('uri') or response.get('data', {}).get('cleanUri')
        if not uri:
            raise ExtractorError('Unable to find stream URI in API response')

        # Download the master playlist with appropriate headers
        m3u8_headers = {
            'origin': 'https://www.flomarching.com',
            'referer': 'https://www.flomarching.com/',
            'user-agent': self._downloader.params.get('http_headers', {}).get('User-Agent') or 'Mozilla/5.0',
        }
        formats = self._extract_m3u8_formats(
            uri, video_id, 'mp4', m3u8_id='hls', fatal=True, headers=m3u8_headers)

        # Get the title
        schedule_url = f'https://api.flomarching.com/api/experiences/web/legacy-core/live-events/{video_id}/schedule?site_id=27&version=1.33.2'
        schedule = self._download_webpage(schedule_url, video_id)
        #title = re.search(r'<h2[^>]>(.*?)</h2>', schedule, re.DOTALL)
        stream_name = response.get('data', {}).get('stream').get('name')
        full_title = datetime.now().strftime('%m-%d-%Y') + ' - ' + stream_name

        result = {
            'id': video_id,
            'title': full_title,
            'formats': formats,
            'is_live': True,
        }
        return result
