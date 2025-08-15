import re
import json
import time
from datetime import datetime, timedelta
from .common import InfoExtractor
from ..utils import (
    ExtractorError,
    traverse_obj,
    urljoin,
)


class FloMarchingIE(InfoExtractor):
    IE_NAME = 'flomarching'
    _VALID_URL = r'https?://(?:www\.)?flomarching\.com/live/(?P<id>\d+)'
    _TESTS = [
        {
            'url': 'https://www.flomarching.com/live/12345',
            'info_dict': {
                'id': '12345',
                'ext': 'mp4',
                'title': 'FloMarching Live Event',
                'live_status': 'is_live',
            },
            'params': {
                'skip_download': True,
            },
        }
    ]

    @staticmethod
    def _configuration_arg(ie_key, option_name, *, casesense=False, default=None):
        """Helper method to get configuration arguments for this extractor"""
        # This would integrate with yt-dlp's --extractor-args system
        # Usage: --extractor-args "flomarching:stream_name=Main Camera;delay_minutes=5;lookback_minutes=10"
        return default

    def _get_extractor_args(self):
        """Parse extractor-specific arguments"""
        return {
            'stream_name': self._configuration_arg('flomarching', 'stream_name'),
            'delay_minutes': int(self._configuration_arg('flomarching', 'delay_minutes', default=0)),
            'lookback_minutes': int(self._configuration_arg('flomarching', 'lookback_minutes', default=0)),
        }

    def _apply_time_parameters(self, stream_uri, lookback_minutes=0):
        """Apply time-based parameters to HLS stream URI if supported"""
        if lookback_minutes <= 0:
            return stream_uri

        # Check if the stream supports DVR/rewind by looking for time-based parameters
        # This is common in HLS streams that support seeking
        if '?' in stream_uri:
            separator = '&'
        else:
            separator = '?'

        # Calculate the lookback timestamp
        lookback_time = datetime.utcnow() - timedelta(minutes=lookback_minutes)

        # Try common HLS DVR parameter formats
        # Format 1: Unix timestamp
        unix_timestamp = int(lookback_time.timestamp())
        modified_uri = f"{stream_uri}{separator}start={unix_timestamp}"

        self.to_screen(f'Attempting to seek back {lookback_minutes} minutes to {lookback_time.strftime("%H:%M:%S UTC")}')

        return modified_uri

    def _filter_stream_by_name(self, stream_list, target_name):
        """Filter streams by name if specified"""
        if not target_name:
            return stream_list

        # Try exact match first
        for stream in stream_list:
            if stream.get('name', '').lower() == target_name.lower():
                self.to_screen(f'Found exact match for stream: {stream.get("name")}')
                return [stream]

        # Try partial match
        matching_streams = []
        for stream in stream_list:
            if target_name.lower() in stream.get('name', '').lower():
                matching_streams.append(stream)

        if matching_streams:
            self.to_screen(f'Found {len(matching_streams)} streams matching "{target_name}": {[s.get("name") for s in matching_streams]}')
            return matching_streams

        # No matches found
        available_streams = [s.get('name', f'Stream {s.get("id")}') for s in stream_list]
        self.report_warning(f'Stream "{target_name}" not found. Available streams: {", ".join(available_streams)}')
        return stream_list
        """Extract JWT token from browser cookies"""
        cookies = self._get_cookies('https://www.flomarching.com')
        jwt_cookie = cookies.get('jwt_token')
        if not jwt_cookie:
            raise ExtractorError(
                'JWT token not found in cookies. Please log in to FloMarching in your browser first.',
                expected=True
            )
        return jwt_cookie.value

    def _get_stream_token(self, stream_id, jwt_token):
        """Get stream token via POST request to FloSports API"""
        token_url = f'https://live-api-3.flosports.tv/streams/{stream_id}/tokens'

        headers = {
            'Authorization': f'Bearer {jwt_token}',
            'X-301-Location': 'web',
            'x-flo-app': 'flosports-webapp',
            'TE': 'trailers',
            'Origin': 'https://www.flomarching.com',
            'Referer': 'https://www.flomarching.com/',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Cache-Control': 'no-cache',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        payload = {
            'adTracking': {
                'appName': 'flosports-web'
            }
        }

        response = self._download_json(
            token_url, stream_id,
            headers=headers,
            data=json.dumps(payload).encode('utf-8'),
            note=f'Getting stream token for stream {stream_id}'
        )

    def _get_event_title(self, event_id, webpage):
        """Get event title from API or webpage"""
        # Try API first
        try:
            schedule_url = f'https://api.flomarching.com/api/experiences/web/legacy-core/live-events/{event_id}/schedule?site_id=27&version=1.33.2'
            schedule_html = self._download_webpage(
                schedule_url, event_id,
                note='Getting event title from API',
                fatal=False
            )
            if schedule_html:
                title_match = self._search_regex(
                    r'<h2[^>]*>([^<]+)</h2>',
                    schedule_html, 'api title', default=None
                )
                if title_match:
                    return title_match.strip()
        except Exception as e:
            self.report_warning(f'Failed to get title from API: {str(e)}')

        # Fallback to webpage H1
        title = self._html_search_regex(
            r'<h1[^>]*class="[^"]*heading-event-title[^"]*"[^>]*>([^<]+)</h1>',
            webpage, 'webpage title', default=None
        )
        if title:
            return title.strip()

        # Final fallback
        return (
            self._og_search_title(webpage, default=None) or
            self._html_search_regex(r'<title>([^<]+)</title>', webpage, 'title', default=f'FloMarching Live Event {event_id}')
        )
    def _real_extract(self, url):
        event_id = self._match_id(url)

        # Parse extractor arguments
        args = self._get_extractor_args()
        stream_name = args.get('stream_name')
        delay_minutes = args.get('delay_minutes', 0)
        lookback_minutes = args.get('lookback_minutes', 0)

        # Handle delay if specified
        if delay_minutes > 0:
            self.to_screen(f'Waiting {delay_minutes} minutes before starting extraction...')
            time.sleep(delay_minutes * 60)

        # Get JWT token from browser cookies
        jwt_token = self._get_jwt_token()

        # Download the main webpage
        webpage = self._download_webpage(url, event_id)

        # Extract and decode the flo-app-state JSON
        app_state_script = self._search_regex(
            r'<script[^>]+id="flo-app-state"[^>]*>(.*?)</script>',
            webpage, 'app state', flags=re.DOTALL
        )

        # Decode the wonky format (&q; -> ")
        decoded_json = app_state_script.replace('&q;', '"')
        try:
            app_state = self._parse_json(decoded_json, event_id)
        except Exception as e:
            raise ExtractorError(f'Failed to parse app state JSON: {str(e)}')

        # Extract stream_list
        stream_list = traverse_obj(app_state, 'stream_list', expected_type=list)
        if not stream_list:
            raise ExtractorError('No streams found in app state')

        # Filter streams by name if specified
        if stream_name:
            stream_list = self._filter_stream_by_name(stream_list, stream_name)

        # Extract metadata from the page
        title = self._get_event_title(event_id, webpage)
        description = self._og_search_description(webpage, default=None)
        thumbnail = self._og_search_thumbnail(webpage, default=None)

        # Build formats from each stream
        formats = []
        subtitles = {}

        for stream in stream_list:
            stream_id = stream.get('id')
            stream_display_name = stream.get('name', f'Stream {stream_id}')

            if not stream_id:
                self.report_warning(f'Skipping stream with no ID: {stream}')
                continue

            try:
                # Get stream token
                token_response = self._get_stream_token(stream_id, jwt_token)

                # Extract stream URI from the data object
                stream_uri = traverse_obj(token_response, ('data', 'uri')) or traverse_obj(token_response, ('data', 'cleanUri'))
                if not stream_uri:
                    self.report_warning(f'No stream URI found for stream {stream_id}')
                    continue

                # Apply time parameters if lookback is requested
                if lookback_minutes > 0:
                    stream_uri = self._apply_time_parameters(stream_uri, lookback_minutes)

                # Get user agent for consistency
                user_agent = (
                    self._downloader.params.get('http_headers', {}).get('User-Agent') or
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                )

                # Set headers for m3u8 requests
                m3u8_headers = {
                    'Origin': 'https://www.flomarching.com',
                    'Referer': 'https://www.flomarching.com/',
                    'User-Agent': user_agent,
                }

                # Extract HLS formats
                stream_formats, stream_subs = self._extract_m3u8_formats_and_subtitles(
                    stream_uri, event_id, 'mp4',
                    entry_protocol='m3u8_native',
                    m3u8_id='hls',
                    headers=m3u8_headers,
                    live=True,
                    fatal=False
                )

                # Add stream name to format IDs for clarity
                for fmt in stream_formats:
                    fmt['format_note'] = stream_display_name
                    if 'format_id' in fmt:
                        fmt['format_id'] = f"{fmt['format_id']}-{stream_display_name.replace(' ', '_').lower()}"

                formats.extend(stream_formats)
                self._merge_subtitles(stream_subs, target=subtitles)

            except ExtractorError as e:
                self.report_warning(f'Failed to extract stream {stream_id} ({stream_display_name}): {str(e)}')
                continue

        if not formats:
            raise ExtractorError('No playable streams found')

        # Modify title if specific stream was selected
        if stream_name and len(stream_list) == 1:
            title = f"{title} - {stream_list[0].get('name', stream_name)}"

        return {
            'id': event_id,
            'title': title,
            'description': description,
            'thumbnail': thumbnail,
            'formats': formats,
            'subtitles': subtitles,
            'live_status': 'is_live',
            'is_live': True,
        }
