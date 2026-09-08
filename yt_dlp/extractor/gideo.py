import datetime
import time

from .common import InfoExtractor
from ..utils import ExtractorError


class GideoStreamotorBaseIE(InfoExtractor):
    """
    Base extractor for channels on the Gideo / Streamotor white-label OTT platform.

    Gideo (gideo.video, "Gideo.TV") powers a family of branded channels (Box5 TV,
    BizTV, HockeyTV, YogaVibes, Gaither TV, ...). They share one backend:
        API : https://api.streamotor.com/api/v1  (HTTP Basic Auth, realm="sm")
        CDN : https://cdn.gideo.video           (Fastly over S3/B2, path-based, likely signed)
    Delivery appears to be token-gated clear HLS (no DRM indicators observed).

    Child classes must define:
        _CHANNEL_DOMAIN   : str  - e.g. 'box5tv.com'
        _CHANNEL_NAME     : str  - human-readable, e.g. 'Box5 TV'
    and may override _API_BASE / _CDN_BASE if a channel is pinned to different hosts.

    NOTE (Phase 1/2): the content->manifest mapping, the Basic credential, and the
    per-user entitlement/token exchange are not observable from outside the app.
    They are isolated behind three seams below and must be filled from static app
    analysis (jadx on video.gideo.box5tv) and/or a logged-in live capture:
        _get_session_token()   - user entitlement
        _resolve_streams()     - content id -> [{id, name, manifest_url}]
    Everything else (URL matching, extractor-args, DVR lookback, HLS assembly) is done.
    """
    _CHANNEL_DOMAIN = None   # Must be overridden
    _CHANNEL_NAME = None     # Must be overridden

    # Confirmed Streamotor/Gideo API surfaces (from static analysis of the Android app
    # video.gideo.box5tv v2.5.0, package com.imavex.channelapp):
    #   _LEGACY_API_BASE : the app's REST API. Endpoints are POST routes (authenticate,
    #                      getChannel, getVideos, subscriberbehavior). GET 404s.
    #   _ROKU_FEED       : older Roku-style XML feed, GET ?cmd=getVideos&ScheduleID=<id>.
    #   _API_BASE (v1)   : newest REST API, HTTP Basic Auth (realm "sm"); web player path.
    #   _CDN_BASE        : object-storage origin the manifests point at (returned by the
    #                      API, not hardcoded). Delivery is ExoPlayer clear HLS.
    _LEGACY_API_BASE = 'https://ott.gideo.video/api/legacy'
    _ROKU_FEED = 'https://iptv.streamotor.com/roku.xml'
    _API_BASE = 'https://api.streamotor.com/api/v1'
    _CDN_BASE = 'https://cdn.gideo.video'

    # Per-channel API key ("ApiKey cannot be null" / getApiKey in the app). Identifies
    # the Box5 channel to the API. Value is not a plain resource literal; TODO Phase 1/2:
    # recover via full DEX decompile (jadx) or by observing a logged-in request.
    _CHANNEL_APIKEY = None

    # Cookie names the web player might use to carry the logged-in session. aMember
    # sets PHPSESSID on .box5tv.com; the Streamotor handoff token name is TBD (RE).
    _SESSION_COOKIE_NAMES = ('PHPSESSID',)

    # DVR seek query param on the HLS origin. Unconfirmed (Flo uses 'start'); the only
    # thing to change in Phase 2 if Streamotor differs (e.g. 'begin'/'dvr').
    _LOOKBACK_PARAM = 'start'

    @property
    def _origin(self):
        return f'https://{self._CHANNEL_DOMAIN}'

    # -- Generic helpers (copy-adapted from flosports.py; intentionally NOT imported,
    #    to keep this provider fully decoupled from the Flo code) -------------------

    def _get_extractor_args(self):
        """
        Parse extractor-specific arguments.

        Usage: --extractor-args "box5tv:stream_name=Main Camera,delay_minutes=5,lookback_minutes=10"
        """
        # casesense=True preserves the camera name's original case for display;
        # matching in _filter_stream_by_name is case-insensitive regardless.
        stream_name_list = self._configuration_arg('stream_name', [], casesense=True)
        delay_list = self._configuration_arg('delay_minutes', ['0'])
        lookback_list = self._configuration_arg('lookback_minutes', ['0'])

        return {
            'stream_name': stream_name_list[0] if stream_name_list else None,
            'delay_minutes': int(delay_list[0]),
            'lookback_minutes': int(lookback_list[0]),
        }

    def _apply_time_parameters(self, stream_uri, lookback_minutes=0):
        """
        Seek an HLS live edge backwards by N minutes, if the origin supports it.

        Flo's Wowza/Transmit origins accept ?start=UNIX. Streamotor's DVR parameter
        name is unconfirmed (TODO Phase 2: verify against a live cdn.gideo.video edge;
        it may be ?start=, ?begin=, or ?dvr=). Kept generic via _LOOKBACK_PARAM so the
        only thing to change later is a class attribute.
        """
        if lookback_minutes <= 0:
            return stream_uri

        separator = '&' if '?' in stream_uri else '?'
        lookback_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=lookback_minutes)
        unix_timestamp = int(lookback_time.timestamp())
        modified_uri = f'{stream_uri}{separator}{self._LOOKBACK_PARAM}={unix_timestamp}'

        self.to_screen(f'Attempting to seek back {lookback_minutes} minutes to {lookback_time.strftime("%H:%M:%S UTC")}')
        return modified_uri

    def _filter_stream_by_name(self, stream_list, target_name):
        """Filter a multicam stream list by (case-insensitive) name, exact then partial."""
        if not target_name:
            return stream_list

        for stream in stream_list:
            if stream.get('name', '').lower() == target_name.lower():
                self.to_screen(f'Found exact match for stream: {stream.get("name")}')
                return [stream]

        matching_streams = [
            stream for stream in stream_list
            if target_name.lower() in stream.get('name', '').lower()
        ]
        if matching_streams:
            names = [s.get('name') for s in matching_streams]
            self.to_screen(f'Found {len(matching_streams)} streams matching "{target_name}": {names}')
            return matching_streams

        available_streams = [s.get('name', f'Stream {s.get("id")}') for s in stream_list]
        self.report_warning(f'Stream "{target_name}" not found. Available streams: {", ".join(available_streams)}')
        return stream_list

    # -- Auth / API seams (Phase 1 RE + Phase 2 live capture) ----------------------

    def _get_session_token(self):
        """
        Return whatever the Streamotor playback API needs to authorize this user.

        Confirmed auth surfaces (app RE):
          - REST: POST {_LEGACY_API_BASE}/authenticate with
                {username, password, apiKey, deviceId} -> authToken
                (the app also supports Google Play IAP: authenticateByReceipt).
          - Web: aMember session cookie on .box5tv.com (PHPSESSID), handed to the
                Streamotor player. Cookie->token handoff name still TBD.

        Default (mirrors the Flo cookies-from-browser posture): read the browser
        session cookie. TODO Phase 1/2: wire the REST authenticate flow (needs the
        channel _CHANNEL_APIKEY) and/or confirm the web cookie->token exchange.
        """
        cookies = self._get_cookies(self._origin)
        for name in self._SESSION_COOKIE_NAMES:
            cookie = cookies.get(name)
            if cookie and cookie.value:
                return cookie.value

        raise ExtractorError(
            f'No {self._CHANNEL_NAME} session cookie found. Log into {self._origin} in your '
            f'browser and pass --cookies-from-browser, then retry.',
            expected=True,
        )

    def _resolve_streams(self, video_id, is_live):
        """
        Map a content id to a list of playable streams:
            [{'id': str, 'name': str, 'manifest_url': str}, ...]

        Two confirmed candidate paths (app RE), both returning the HLS manifest URL(s)
        that the API points at cdn.gideo.video (URLs are server-provided, not built here):
          A) REST : POST {_LEGACY_API_BASE}/getVideos with {apiKey, videoId|streamId,
                    authToken, deviceId} -> JSON with the stream/manifest URL(s).
          B) Roku : GET {_ROKU_FEED}?cmd=getVideos&ScheduleID=<id> -> XML listing.

        Blocked on two inputs that need either a full DEX decompile or a logged-in
        session: the channel _CHANNEL_APIKEY and a valid authToken/session. Fails
        loud-but-clear rather than guessing, so no wrong endpoint ships.
        """
        if not self._CHANNEL_APIKEY:
            raise ExtractorError(
                f'{self._CHANNEL_NAME} playback needs the channel apiKey and a logged-in '
                'session, which are pending reverse-engineering (see box5-capture '
                'docs/streamotor-api-notes.md). URL matching, extractor-args, DVR '
                'lookback, and HLS assembly are all wired and ready.',
                expected=True,
            )
        raise ExtractorError('Gideo/Streamotor stream resolution not yet implemented', expected=True)

    def _extract_from_manifest(self, manifest_url, video_id, is_live, display_name, lookback_minutes=0):
        """Build yt-dlp formats/subtitles from one HLS manifest URL. Fully implemented."""
        if lookback_minutes > 0 and is_live:
            manifest_url = self._apply_time_parameters(manifest_url, lookback_minutes)

        headers = {
            'Origin': self._origin,
            'Referer': f'{self._origin}/',
        }
        formats, subtitles = self._extract_m3u8_formats_and_subtitles(
            manifest_url, video_id, 'mp4',
            entry_protocol='m3u8_native',
            m3u8_id='hls',
            headers=headers,
            live=is_live,
            fatal=False,
        )
        # Label formats with the camera/stream name so multicam captures are selectable.
        slug = display_name.replace(' ', '_').lower()
        for fmt in formats:
            fmt['format_note'] = display_name
            if 'format_id' in fmt:
                fmt['format_id'] = f'{fmt["format_id"]}-{slug}'
        return formats, subtitles

    # -- Orchestration -------------------------------------------------------------

    def _real_extract(self, url):
        mobj = self._match_valid_url(url)
        video_id = mobj.group('id')
        content_type = mobj.group('type')
        is_live = content_type == 'live'

        args = self._get_extractor_args()
        stream_name = args.get('stream_name')
        delay_minutes = args.get('delay_minutes', 0)
        lookback_minutes = args.get('lookback_minutes', 0)

        if delay_minutes > 0:
            self.to_screen(f'Waiting {delay_minutes} minutes before starting extraction...')
            time.sleep(delay_minutes * 60)

        streams = self._resolve_streams(video_id, is_live)
        if stream_name:
            streams = self._filter_stream_by_name(streams, stream_name)
        if not streams:
            raise ExtractorError('No playable streams found')

        formats, subtitles = [], {}
        for stream in streams:
            manifest_url = stream.get('manifest_url')
            display_name = stream.get('name') or f'Stream {stream.get("id")}'
            if not manifest_url:
                self.report_warning(f'No manifest URL for stream {stream.get("id")}')
                continue
            try:
                s_formats, s_subs = self._extract_from_manifest(
                    manifest_url, video_id, is_live, display_name, lookback_minutes)
            except ExtractorError as e:
                self.report_warning(f'Failed to extract stream {stream.get("id")} ({display_name}): {e}')
                continue
            formats.extend(s_formats)
            self._merge_subtitles(s_subs, target=subtitles)

        if not formats:
            raise ExtractorError('No playable streams found')

        title = video_id
        if stream_name and len(streams) == 1:
            title = f'{title} - {streams[0].get("name", stream_name)}'

        return {
            'id': video_id,
            'title': title,  # TODO Phase 1: pull real title from the Streamotor metadata
            'formats': formats,
            'subtitles': subtitles,
            'live_status': 'is_live' if is_live else 'was_live',
            'is_live': is_live,
        }


class Box5TVIE(GideoStreamotorBaseIE):
    """Box5 TV (marching bands, drum corps, color guard) on the Gideo/Streamotor platform."""
    IE_NAME = 'box5tv'
    _CHANNEL_DOMAIN = 'box5tv.com'
    _CHANNEL_NAME = 'Box5 TV'

    # NOTE: the authenticated watch-URL scheme is behind the aMember paywall and
    # unconfirmed. This provisional pattern covers the livestream subdomain and the
    # apex with a type segment + id/slug. TODO Phase 2: confirm real live/VOD URLs.
    _VALID_URL = r'''(?x)
        https?://(?:www\.|livestream\.)?box5tv\.com/
        (?:livestream/)?
        (?P<type>live|video|vod|watch|event)/(?P<id>[\w-]+)'''
    _TESTS = [{
        # Provisional URL shapes (only_matching until the real scheme is confirmed).
        'url': 'https://livestream.box5tv.com/live/12345',
        'only_matching': True,
    }, {
        'url': 'https://box5tv.com/video/12345-boa-grand-nationals-2025',
        'only_matching': True,
    }, {
        'url': 'https://livestream.box5tv.com/watch/some-event-slug',
        'only_matching': True,
    }]
