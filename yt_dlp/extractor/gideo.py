import re
import urllib.parse

from .common import InfoExtractor
from ..utils import (
    ExtractorError,
    clean_html,
    url_or_none,
)
from ..utils.traversal import traverse_obj


class Box5TVIE(InfoExtractor):
    """
    Box5 TV (marching bands, drum corps, color guard), a channel on the Gideo /
    Streamotor white-label OTT platform.

    Box5 fronts Streamotor with a WordPress/WooCommerce + aMember paywall. The watch
    page at `/watch/?stream=<id>&stream-type=<multi|high>` embeds the Streamotor
    player - a script at `app.streamotor.com/player/v2/embed.php` whose `livesrc`
    query param carries the (already entitled) HLS master URL - but only for a
    logged-in, entitled session; otherwise it 302s to the login page. So a session
    cookie (PHPSESSID on .box5tv.com) is required: pass --cookies-from-browser or
    --cookies cookies.txt.

    Each `stream-type` is a distinct camera angle with its own manifest and token, so
    a full multicam capture resolves every angle the event offers. Delivery is clear
    HLS (no DRM); the Fastly-fronted origin gates only the master playlist on the
    opaque `?t=` token and serves the media segments openly.

    DVR: the entitled master manifest is itself a rolling DVR playlist (several hours
    of back-content). Use `--live-from-start` to pull from the oldest available
    segment; this origin has no query-param seek. For arbitrary-range after-the-fact
    recovery, see scripts/recover_box5.ps1.
    """
    IE_NAME = 'box5tv'
    _CHANNEL_NAME = 'Box5 TV'
    _ORIGIN = 'https://box5tv.com'
    _EMBED_HOST = 'app.streamotor.com'

    # The id lives in the query (stream=<numeric id>); the angle is stream-type.
    _VALID_URL = r'https?://(?:www\.)?box5tv\.com/watch/?\?(?:[^#]*&)?stream=(?P<id>\d+)'
    _TESTS = [{
        'url': 'https://box5tv.com/watch/?stream=38418&stream-type=multi',
        'only_matching': True,
    }, {
        'url': 'https://box5tv.com/watch/?stream=38418&stream-type=high',
        'only_matching': True,
    }, {
        # Order of query params must not matter.
        'url': 'https://box5tv.com/watch/?stream-type=multi&stream=36272',
        'only_matching': True,
    }]

    def _get_extractor_args(self):
        """
        Parse extractor-specific arguments.

        Usage: --extractor-args "box5tv:stream_name=High Cam"

        `stream_name` picks a single camera angle out of a multicam event (matched
        case-insensitively, exact before partial). Absent, every angle is returned.
        """
        # casesense=True preserves the angle name's original case for display;
        # matching in _filter_stream_by_name is case-insensitive regardless.
        stream_name_list = self._configuration_arg('stream_name', [], casesense=True)
        return {
            'stream_name': stream_name_list[0] if stream_name_list else None,
        }

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

    def _watch_url(self, video_id, stream_type):
        return f'{self._ORIGIN}/watch/?stream={video_id}&stream-type={stream_type}'

    def _master_url_from_embed(self, html, note_source):
        """
        Pull the HLS master manifest URL out of the Streamotor player embed.

        The embed is `.../embed.php?livesrc=<urlencoded manifest>&...`; the manifest
        value is percent-encoded (so it contains no raw '&') and already carries its
        own `?t=` entitlement token. Returns None if no embed is present, so callers
        can tell "not entitled / wrong page" apart from a hard failure.
        """
        match = re.search(
            rf'{re.escape(self._EMBED_HOST)}/player/v2/embed\.php\?livesrc=(?P<src>[^&"\']+)',
            html)
        if not match:
            return None

        manifest_url = urllib.parse.unquote(match.group('src'))
        if not url_or_none(manifest_url):
            self.report_warning(f'Found a Streamotor embed on {note_source} but its livesrc was not a URL')
            return None
        return manifest_url

    def _angles_for_stream(self, html, video_id):
        """
        Discover the camera angles offered for this stream from the watch page.

        The page lists them as anchors
            <a ... href=".../watch/?stream=<id>&#038;stream-type=<type>">Label</a>
        (&#038; is an HTML-encoded '&'). Scoped to video_id so we don't pick up the
        other events' cards. Returns an ordered, de-duplicated [(stream_type, label)].
        """
        pattern = (
            rf'/watch/\?stream={re.escape(video_id)}(?:&#0?38;|&amp;|&)stream-type='
            r'(?P<type>[a-z0-9-]+)"[^>]*>(?P<label>[^<]+)</a>')
        angles, seen = [], set()
        for m in re.finditer(pattern, html):
            stream_type = m.group('type')
            if stream_type in seen:
                continue
            seen.add(stream_type)
            angles.append((stream_type, clean_html(m.group('label')).strip()))
        return angles

    def _event_title(self, html):
        """
        Best-effort event title from the selected schedule card, which carries the
        round and venue, e.g. a card holding box5-event-stage "PRELIMS" and
        box5-card-schedule__name "Fairfield Jr./Sr. H.S., IN" -> "PRELIMS - Fairfield
        Jr./Sr. H.S., IN". Falls back to og:title/<title> if the card isn't found.
        """
        card = self._search_regex(
            r'(<li[^>]*box5-event-card--selected.*?</li>)',
            html, 'event card', default=None, flags=re.DOTALL)
        if card:
            stage = self._search_regex(
                r'class="box5-event-stage"[^>]*>([^<]+)', card, 'stage', default=None)
            venue = self._search_regex(
                r'class="box5-card-schedule__name"[^>]*>([^<]+)', card, 'venue', default=None)
            parts = [clean_html(p).strip() for p in (stage, venue) if p]
            if parts:
                return ' - '.join(parts)
        return self._og_search_title(html, default=None) or self._html_extract_title(html, default=None)

    def _extract_from_manifest(self, manifest_url, video_id, is_live, display_name):
        """Build yt-dlp formats/subtitles from one HLS master URL."""
        # The CDN gates only the master on the ?t= token and serves segments openly;
        # no Origin/Referer is required, but sending them matches the real player and
        # costs nothing if the origin ever tightens.
        headers = {
            'Origin': self._ORIGIN,
            'Referer': f'{self._ORIGIN}/',
        }
        formats, subtitles = self._extract_m3u8_formats_and_subtitles(
            manifest_url, video_id, 'mp4',
            entry_protocol='m3u8_native',
            m3u8_id='hls',
            headers=headers,
            live=is_live,
            fatal=False,
        )
        # Label formats with the angle name so multicam captures are selectable and
        # their format ids stay unique across angles (e.g. "hls-1080-high_cam").
        slug = re.sub(r'\W+', '_', display_name).strip('_').lower()
        for fmt in formats:
            fmt['format_note'] = display_name
            if 'format_id' in fmt:
                fmt['format_id'] = f'{fmt["format_id"]}-{slug}'
        return formats, subtitles

    def _resolve_one(self, video_id, stream_type, label, html=None):
        """Resolve a single angle to a stream dict, fetching its page unless supplied."""
        if html is None:
            html = self._download_webpage(
                self._watch_url(video_id, stream_type), video_id,
                note=f'Downloading {label} watch page',
                errnote=f'Could not load {label} watch page')

        manifest_url = self._master_url_from_embed(html, f'{label} page')
        if not manifest_url:
            self.report_warning(
                f'No entitled Streamotor embed for "{label}" (stream {video_id}). '
                f'Confirm your {self._CHANNEL_NAME} login covers this event.')
            return None

        # A live DVR playlist has no #EXT-X-ENDLIST; once the event ends the origin
        # adds it and the asset becomes VOD. Non-fatal: default to live on any hiccup.
        media = self._download_webpage(
            manifest_url, video_id, note=f'Checking {label} manifest state',
            errnote=False, fatal=False)
        is_live = '#EXT-X-ENDLIST' not in (media or '')
        return {
            'id': f'{video_id}-{stream_type}',
            'name': label,
            'manifest_url': manifest_url,
            'is_live': is_live,
            'event_title': self._event_title(html),
        }

    def _resolve_streams(self, url, video_id):
        """Map a watch URL to [{'id','name','manifest_url','is_live','event_title'}]."""
        requested_type = traverse_obj(urllib.parse.parse_qs(
            urllib.parse.urlparse(url).query), ('stream-type', 0))

        # Download the requested page once (default to the multi angle), detecting the
        # login redirect that means "not logged in / not entitled".
        first_type = requested_type or 'multi'
        html, urlh = self._download_webpage_handle(
            self._watch_url(video_id, first_type), video_id,
            note='Downloading watch page')
        if '/login' in urlh.url or '_amember_redirect_url' in urlh.url:
            raise ExtractorError(
                f'{self._CHANNEL_NAME} redirected to login. Log into {self._ORIGIN} in your '
                'browser and pass --cookies-from-browser (or --cookies cookies.txt), then retry.',
                expected=True)

        angles = self._angles_for_stream(html, video_id)
        # If the user pinned a stream-type, honour only that; otherwise capture every
        # angle the page offers (falling back to the one we fetched if none parsed).
        if requested_type:
            angles = [(t, lbl) for t, lbl in angles if t == requested_type] \
                or [(requested_type, requested_type.title())]
        elif not angles:
            angles = [(first_type, first_type.title())]

        streams = []
        for stream_type, label in angles:
            # Reuse the page we already have for the one we fetched.
            page = html if stream_type == first_type else None
            resolved = self._resolve_one(video_id, stream_type, label, html=page)
            if resolved:
                streams.append(resolved)
        return streams

    def _real_extract(self, url):
        video_id = self._match_id(url)

        args = self._get_extractor_args()
        stream_name = args.get('stream_name')

        streams = self._resolve_streams(url, video_id)
        if stream_name:
            streams = self._filter_stream_by_name(streams, stream_name)
        if not streams:
            raise ExtractorError('No playable streams found')

        formats, subtitles = [], {}
        for stream in streams:
            manifest_url = stream.get('manifest_url')
            display_name = stream.get('name') or f'Stream {stream.get("id")}'
            try:
                s_formats, s_subs = self._extract_from_manifest(
                    manifest_url, video_id, stream.get('is_live', True), display_name)
            except ExtractorError as e:
                self.report_warning(f'Failed to extract stream {stream.get("id")} ({display_name}): {e}')
                continue
            formats.extend(s_formats)
            self._merge_subtitles(s_subs, target=subtitles)

        if not formats:
            raise ExtractorError('No playable streams found')

        # Prefer a real event title (the venue/show on the watch page) over the id,
        # and append the single angle's name when exactly one was requested.
        title = traverse_obj(streams, (0, 'event_title')) or video_id
        if stream_name and len(streams) == 1:
            title = f'{title} - {streams[0].get("name", stream_name)}'

        is_live = bool(traverse_obj(streams, (0, 'is_live')))
        return {
            'id': video_id,
            'title': title,
            'formats': formats,
            'subtitles': subtitles,
            'live_status': 'is_live' if is_live else 'was_live',
            'is_live': is_live,
        }
