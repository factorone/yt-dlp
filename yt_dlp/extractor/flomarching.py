import re
from .common import InfoExtractor
from ..utils import ExtractorError, urlencode_postdata
import json

class FloMarchingLiveIE(InfoExtractor):
    _VALID_URL = r'https://www.flomarching.com/live/(?P<id>\d+)'
    _LOGIN_URL = 'https://www.flomarching.com/login'
    _STREAM_INFO_URL = 'https://www.flomarching.com/api/live/{video_id}'

    _TEST = {
        'url': 'https://www.flomarching.com/live/164101',
        'info_dict': {
            'id': '164101',
            'title': 'FloMarching Live Stream 164101',
            'formats': 'count:1',
        },
        'params': {
            'skip_download': True,
        },
        'skip': 'Requires valid FloMarching login credentials',
    }

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

        # Download the webpage to extract the stream_id
        webpage = self._download_webpage(url, video_id)
        m = re.search(r'stream_id\s*=\s*(\d+)', webpage)
        if not m:
            raise ExtractorError('Unable to find stream_id in page HTML')
        stream_id = m.group(1)

        api_url = f'https://live-api-3.flosports.tv/streams/{stream_id}/tokens'
        headers = {
            'accept': 'application/json, text/plain, */*',
            'content-type': 'application/json',
            'origin': 'https://www.flomarching.com',
            'referer': 'https://www.flomarching.com/',
            'user-agent': self._downloader.params.get('http_headers', {}).get('User-Agent') or 'Mozilla/5.0',
            'x-301-location': 'web',
            'x-flo-app': 'flosports-webapp',
        }
        data = json.dumps({"adTracking": {"appName": "flosports-web"}}).encode('utf-8')

        response = self._download_json(
            api_url, video_id, note='Requesting stream token',
            data=data, headers=headers, expected_status=200)

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
        title = response.get('data', {}).get('stream', {}).get('name') or f'FloMarching Live Stream {video_id}'
        return {
            'id': video_id,
            'title': title,
            'formats': formats,
            'is_live': True,
        }
