import datetime
import json
import re
import time

from .common import InfoExtractor
from ..utils import (
    ExtractorError,
    traverse_obj,
)


class FloSportsBaseIE(InfoExtractor):
    """
    Base extractor for FloSports properties.

    Child classes must define:
        _FLO_DOMAIN: str - e.g., 'flomarching.com'
        _FLO_SITE_ID: int - site ID used in API calls
        _FLO_PROPERTY_NAME: str - human-readable name, e.g., 'FloMarching'
    """
    _FLO_DOMAIN = None  # Must be overridden
    _FLO_SITE_ID = None  # Must be overridden
    _FLO_PROPERTY_NAME = None  # Must be overridden

    def _get_extractor_args(self):
        """
        Parse extractor-specific arguments.

        Usage: --extractor-args "flosports:stream_name=Main Camera,delay_minutes=5,lookback_minutes=10"
        """
        stream_name_list = self._configuration_arg('stream_name', [])
        delay_list = self._configuration_arg('delay_minutes', ['0'])
        lookback_list = self._configuration_arg('lookback_minutes', ['0'])

        return {
            'stream_name': stream_name_list[0] if stream_name_list else None,
            'delay_minutes': int(delay_list[0]),
            'lookback_minutes': int(lookback_list[0]),
        }

    def _apply_time_parameters(self, stream_uri, lookback_minutes=0):
        """Apply time-based parameters to HLS stream URI if supported"""
        if lookback_minutes <= 0:
            return stream_uri

        if '?' in stream_uri:
            separator = '&'
        else:
            separator = '?'

        lookback_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=lookback_minutes)
        unix_timestamp = int(lookback_time.timestamp())
        modified_uri = f'{stream_uri}{separator}start={unix_timestamp}'

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
        matching_streams = [
            stream for stream in stream_list
            if target_name.lower() in stream.get('name', '').lower()
        ]

        if matching_streams:
            names = [s.get('name') for s in matching_streams]
            self.to_screen(f'Found {len(matching_streams)} streams matching "{target_name}": {names}')
            return matching_streams

        # No matches found
        available_streams = [s.get('name', f'Stream {s.get("id")}') for s in stream_list]
        self.report_warning(f'Stream "{target_name}" not found. Available streams: {", ".join(available_streams)}')
        return stream_list

    @property
    def _flo_origin(self):
        return f'https://www.{self._FLO_DOMAIN}'

    def _get_jwt_token(self):
        """Extract JWT token from browser cookies"""
        cookies = self._get_cookies(self._flo_origin)
        jwt_cookie = cookies.get('jwt_token')
        if not jwt_cookie:
            raise ExtractorError(
                f'JWT token not found in cookies. Please log in to {self._FLO_PROPERTY_NAME} in your browser first.',
                expected=True,
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
            'Origin': self._flo_origin,
            'Referer': f'{self._flo_origin}/',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Cache-Control': 'no-cache',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }

        payload = {
            'adTracking': {
                'appName': 'flosports-web',
            },
        }

        return self._download_json(
            token_url, stream_id,
            headers=headers,
            data=json.dumps(payload).encode('utf-8'),
            note=f'Getting stream token for stream {stream_id}',
        )

    def _get_event_title(self, event_id, webpage):
        """Get event title from API or webpage"""
        # Try API first
        if self._FLO_SITE_ID:
            try:
                schedule_url = (
                    f'https://api.{self._FLO_DOMAIN}/api/experiences/web/legacy-core/'
                    f'live-events/{event_id}/schedule?site_id={self._FLO_SITE_ID}&version=1.33.2'
                )
                schedule_html = self._download_webpage(
                    schedule_url, event_id,
                    note='Getting event title from API',
                    fatal=False,
                )
                if schedule_html:
                    title_match = self._search_regex(
                        r'<h2[^>]*>([^<]+)</h2>',
                        schedule_html, 'api title', default=None,
                    )
                    if title_match:
                        return title_match.strip()
            except Exception as e:
                self.report_warning(f'Failed to get title from API: {e}')

        # Fallback to webpage H1
        title = self._html_search_regex(
            r'<h1[^>]*class="[^"]*heading-event-title[^"]*"[^>]*>([^<]+)</h1>',
            webpage, 'webpage title', default=None,
        )
        if title:
            return title.strip()

        # Final fallback
        return (
            self._og_search_title(webpage, default=None)
            or self._html_search_regex(
                r'<title>([^<]+)</title>', webpage, 'title',
                default=f'{self._FLO_PROPERTY_NAME} Event {event_id}',
            )
        )

    def _extract_vod(self, video_id):
        """
        Extract VOD using the public video API.

        The /api/videos/{id} endpoint returns a 'playlist' field pointing to
        CloudFront, which serves the original HLS manifest with full audio.
        Flo's player uses the 'playlist_no_audio' (Transmit.live) field instead,
        which strips audio references from the manifest. No auth required.
        """
        api_url = f'https://api.flosports.tv/api/videos/{video_id}'
        video_data = self._download_json(
            api_url, video_id,
            note='Fetching video metadata from FloSports API',
            fatal=False,
        )

        if not video_data:
            return None

        # API wraps response in {"data": {...}}
        video_info = video_data.get('data', video_data)

        # Prefer CloudFront playlist (has audio) over Transmit.live (stripped)
        playlist_url = video_info.get('playlist')

        if not playlist_url:
            self.report_warning(
                'No CloudFront playlist URL found in video API response. '
                'Falling back to standard extraction.')
            return None

        no_audio = video_info.get('no_audio', False)
        if no_audio:
            self.to_screen(
                'Video API claims no_audio=true, but CloudFront playlist '
                'contains full audio. Using CloudFront URL.')

        title = video_info.get('title', f'{self._FLO_PROPERTY_NAME} Video {video_id}')
        duration = video_info.get('duration')
        thumbnail = video_info.get('asset_url')

        m3u8_headers = {
            'Origin': self._flo_origin,
            'Referer': f'{self._flo_origin}/',
        }

        formats, subtitles = self._extract_m3u8_formats_and_subtitles(
            playlist_url, video_id, 'mp4',
            entry_protocol='m3u8_native',
            m3u8_id='hls',
            headers=m3u8_headers,
            live=False,
            fatal=False,
        )

        if not formats:
            self.report_warning('No formats extracted from CloudFront manifest.')
            return None

        return {
            'id': video_id,
            'title': title,
            'duration': duration,
            'thumbnail': thumbnail,
            'formats': formats,
            'subtitles': subtitles,
            'live_status': 'was_live',
            'is_live': False,
        }

    def _real_extract(self, url):
        mobj = self._match_valid_url(url)
        event_id = mobj.group('id')
        content_type = mobj.group('type')

        is_live = content_type == 'live'

        # VOD: use the public video API (no auth needed, has audio)
        if not is_live:
            vod_result = self._extract_vod(event_id)
            if vod_result:
                return vod_result
            self.report_warning('VOD API extraction failed, falling back to stream token flow')

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
            webpage, 'app state', flags=re.DOTALL,
        )

        # Decode the wonky format (&q; -> ")
        decoded_json = app_state_script.replace('&q;', '"')
        try:
            app_state = self._parse_json(decoded_json, event_id)
        except Exception as e:
            raise ExtractorError(f'Failed to parse app state JSON: {e}')

        # Extract stream data - structure varies between live and VOD
        # Live events: nested under Firebase URL key -> body -> streams (dict)
        # VOD: may have stream_list directly
        stream_list = None
        event_body = None

        # Try to find Firebase-style nested data first (live events)
        for key, value in app_state.items():
            if 'firebaseio.com' in key and isinstance(value, dict):
                event_body = value.get('body', {})
                streams_dict = event_body.get('streams', {})
                if streams_dict and isinstance(streams_dict, dict):
                    # Convert streams dict to list, adding 'id' from the key
                    stream_list = [
                        {'id': stream_id, **stream_data}
                        for stream_id, stream_data in streams_dict.items()
                        if stream_data.get('isActive', True)
                    ]
                    break

        # Fallback to stream_list if present (older format or VOD)
        if not stream_list:
            stream_list = traverse_obj(app_state, 'stream_list', expected_type=list)

        if not stream_list:
            raise ExtractorError('No streams found in app state')

        # Filter streams by name if specified
        if stream_name:
            stream_list = self._filter_stream_by_name(stream_list, stream_name)

        # Extract metadata - prefer event_body data if available
        if event_body:
            title = event_body.get('name') or event_body.get('shortName')
        else:
            title = None
        if not title:
            title = self._get_event_title(event_id, webpage)
        description = self._og_search_description(webpage, default=None)
        thumbnail = self._og_search_thumbnail(webpage, default=None)

        # Build formats from each stream
        formats = []
        subtitles = {}

        for stream in stream_list:
            # API requires numeric stream ID
            stream_id = stream.get('id')
            stream_display_name = stream.get('name', f'Stream {stream_id}')

            if not stream_id:
                self.report_warning(f'Skipping stream with no ID: {stream}')
                continue

            try:
                # Get stream token
                token_response = self._get_stream_token(stream_id, jwt_token)

                # Extract stream URI from the data object
                stream_uri = (
                    traverse_obj(token_response, ('data', 'uri'))
                    or traverse_obj(token_response, ('data', 'cleanUri'))
                )
                if not stream_uri:
                    self.report_warning(f'No stream URI found for stream {stream_id}')
                    continue

                # Apply time parameters if lookback is requested
                if lookback_minutes > 0:
                    stream_uri = self._apply_time_parameters(stream_uri, lookback_minutes)

                # Get user agent for consistency
                user_agent = (
                    self._downloader.params.get('http_headers', {}).get('User-Agent')
                    or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )

                # Set headers for m3u8 requests
                m3u8_headers = {
                    'Origin': self._flo_origin,
                    'Referer': f'{self._flo_origin}/',
                    'User-Agent': user_agent,
                }

                # Extract HLS formats
                stream_formats, stream_subs = self._extract_m3u8_formats_and_subtitles(
                    stream_uri, event_id, 'mp4',
                    entry_protocol='m3u8_native',
                    m3u8_id='hls',
                    headers=m3u8_headers,
                    live=is_live,
                    fatal=False,
                )

                # Add stream name to format IDs for clarity
                for fmt in stream_formats:
                    fmt['format_note'] = stream_display_name
                    if 'format_id' in fmt:
                        fmt['format_id'] = f'{fmt["format_id"]}-{stream_display_name.replace(" ", "_").lower()}'

                formats.extend(stream_formats)
                self._merge_subtitles(stream_subs, target=subtitles)

            except ExtractorError as e:
                self.report_warning(f'Failed to extract stream {stream_id} ({stream_display_name}): {e}')
                continue

        if not formats:
            raise ExtractorError('No playable streams found')

        # Modify title if specific stream was selected
        if stream_name and len(stream_list) == 1:
            title = f'{title} - {stream_list[0].get("name", stream_name)}'

        return {
            'id': event_id,
            'title': title,
            'description': description,
            'thumbnail': thumbnail,
            'formats': formats,
            'subtitles': subtitles,
            'live_status': 'is_live' if is_live else 'was_live',
            'is_live': is_live,
        }


class FloMarchingIE(FloSportsBaseIE):
    """Extractor for FloMarching (DCI, WGI, marching bands)"""
    IE_NAME = 'flomarching'
    _FLO_DOMAIN = 'flomarching.com'
    _FLO_SITE_ID = 27
    _FLO_PROPERTY_NAME = 'FloMarching'

    _VALID_URL = r'https?://(?:www\.)?flomarching\.com/(?P<type>live|video)/(?P<id>\d+)(?:-[\w-]+)?'
    _TESTS = [{
        # Live event URL pattern
        'url': 'https://www.flomarching.com/live/12345',
        'only_matching': True,
    }, {
        # VOD with slug
        'url': 'https://www.flomarching.com/video/12345-dci-world-championships-2024',
        'only_matching': True,
    }, {
        # VOD without www
        'url': 'https://flomarching.com/video/67890-wgi-finals',
        'only_matching': True,
    }]


class FloRacingIE(FloSportsBaseIE):
    """Extractor for FloRacing (sprint cars, midgets, dirt track)"""
    IE_NAME = 'floracing'
    _FLO_DOMAIN = 'floracing.com'
    _FLO_SITE_ID = 20  # TODO: Verify this site ID
    _FLO_PROPERTY_NAME = 'FloRacing'

    _VALID_URL = r'https?://(?:www\.)?floracing\.com/(?P<type>live|video)/(?P<id>\d+)(?:-[\w-]+)?'
    _TESTS = [{
        'url': 'https://www.floracing.com/live/12345',
        'only_matching': True,
    }, {
        'url': 'https://www.floracing.com/video/12345-knoxville-nationals',
        'only_matching': True,
    }]


class FloWrestlingIE(FloSportsBaseIE):
    """Extractor for FloWrestling"""
    IE_NAME = 'flowrestling'
    _FLO_DOMAIN = 'flowrestling.com'
    _FLO_SITE_ID = 2  # TODO: Verify this site ID
    _FLO_PROPERTY_NAME = 'FloWrestling'

    _VALID_URL = r'https?://(?:www\.)?flowrestling\.com/(?P<type>live|video)/(?P<id>\d+)(?:-[\w-]+)?'
    _TESTS = [{
        'url': 'https://www.flowrestling.com/live/12345',
        'only_matching': True,
    }]
