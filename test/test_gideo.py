#!/usr/bin/env python3

# Allow direct execution
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from test.helper import FakeYDL
from yt_dlp.extractor.gideo import Box5TVIE
from yt_dlp.utils import ExtractorError


# A watch page mirroring the real Box5 markup: one Streamotor embed, two camera-link
# angles per event card (with the HTML-encoded &#038; ampersand), and a selected
# schedule card carrying the round (stage) and venue. A second, non-selected card is
# present so angle-discovery scoping and title selection can be exercised.
_WATCH_HTML = '''<!doctype html><html><head><title>Watch &#8211; BOX5-TV</title></head><body>
<h1 class="player__title">Multi Cam</h1>
<script src="https://app.streamotor.com/player/v2/embed.php?livesrc=https%3A%2F%2Fbox5.gideo.video%2Fhls%2F198%2Fdirectorscut.m3u8%3Ft%3DTOKEN123&plugins=box5"></script>
<ul>
  <li data-box5-stream-id="38418" class="box5-event-card box5-event-card--selected" data-stream="38418">
    <div class="box5-event-stage">PRELIMS</div>
    <div class="box5-event-date">October 16, 2026</div>
    <div class="box5-camera-links">
      <a class="box5-camera-link" href="https://box5tv.com/watch/?stream=38418&#038;stream-type=multi" aria-current="true">Multi Cam</a>
      <a class="box5-camera-link" href="https://box5tv.com/watch/?stream=38418&#038;stream-type=high">High Cam</a>
    </div>
    <div class="box5-card-schedule box5-card-schedule--live">
      <span class="box5-card-schedule__label">Live now</span>
      <span class="box5-card-schedule__name">Fairfield Jr./Sr. H.S., IN</span>
      <span class="box5-card-schedule__time">9:15 AM Eastern</span>
    </div>
  </li>
  <li data-box5-stream-id="36272" class="box5-event-card" data-stream="36272">
    <div class="box5-camera-links">
      <a class="box5-camera-link" href="https://box5tv.com/watch/?stream=36272&#038;stream-type=multi">Multi Cam</a>
      <a class="box5-camera-link" href="https://box5tv.com/watch/?stream=36272&#038;stream-type=high">High Cam</a>
    </div>
  </li>
</ul>
</body></html>'''


class _FakeURLH:
    """Minimal stand-in for an HTTP response handle (only .url is read)."""
    def __init__(self, url):
        self.url = url


class TestBox5TVValidURL(unittest.TestCase):
    def test_matches_watch_urls(self):
        for url in (
            'https://box5tv.com/watch/?stream=38418&stream-type=multi',
            'https://box5tv.com/watch/?stream=38418&stream-type=high',
            'https://www.box5tv.com/watch/?stream=36272',
            'https://box5tv.com/watch/?stream-type=multi&stream=36272',  # reordered params
            'https://box5tv.com/watch?stream=1',  # no trailing slash before query
        ):
            self.assertTrue(Box5TVIE.suitable(url), f'should match: {url}')

    def test_rejects_non_watch_urls(self):
        for url in (
            'https://box5tv.com/',
            'https://box5tv.com/watch/?foo=bar',               # watch page but no stream id
            'https://box5tv.com/watch/?stream=abc',            # non-numeric id
            'https://livestream.box5tv.com/watch/?stream=1',   # content lives on the apex, not this subdomain
            'https://www.flomarching.com/live/12345',          # different provider
        ):
            self.assertFalse(Box5TVIE.suitable(url), f'should not match: {url}')

    def test_captures_numeric_id(self):
        self.assertEqual(
            Box5TVIE._match_valid_url('https://box5tv.com/watch/?stream=38418&stream-type=multi').group('id'),
            '38418')
        # id is read from the query regardless of param order
        self.assertEqual(
            Box5TVIE._match_valid_url('https://box5tv.com/watch/?stream-type=high&stream=36272').group('id'),
            '36272')


class TestBox5TVExtractorArgs(unittest.TestCase):
    def test_defaults_to_no_stream_name(self):
        self.assertIsNone(Box5TVIE(FakeYDL())._get_extractor_args()['stream_name'])

    def test_parses_stream_name(self):
        ydl = FakeYDL({'extractor_args': {'box5tv': {'stream_name': ['High Cam']}}})
        self.assertEqual(Box5TVIE(ydl)._get_extractor_args()['stream_name'], 'High Cam')


class TestBox5TVFilter(unittest.TestCase):
    def setUp(self):
        self.ie = Box5TVIE(FakeYDL())

    def test_none_returns_all(self):
        streams = [{'id': '1', 'name': 'Multi Cam'}, {'id': '2', 'name': 'High Cam'}]
        self.assertEqual(self.ie._filter_stream_by_name(streams, None), streams)

    def test_exact_match_returns_single(self):
        streams = [{'id': '1', 'name': 'Multi Cam'}, {'id': '2', 'name': 'High Cam'}]
        self.assertEqual(
            self.ie._filter_stream_by_name(streams, 'high cam'),
            [{'id': '2', 'name': 'High Cam'}])

    def test_partial_match_returns_subset(self):
        streams = [
            {'id': '1', 'name': 'Multi Cam'},
            {'id': '2', 'name': 'High Cam'},
            {'id': '3', 'name': 'Field Level'},
        ]
        result = self.ie._filter_stream_by_name(streams, 'cam')
        self.assertEqual([s['id'] for s in result], ['1', '2'])

    def test_no_match_returns_all(self):
        streams = [{'id': '1', 'name': 'Multi Cam'}]
        self.assertEqual(self.ie._filter_stream_by_name(streams, 'nonexistent'), streams)


class TestBox5TVParsing(unittest.TestCase):
    def setUp(self):
        self.ie = Box5TVIE(FakeYDL())

    def test_master_url_from_embed_unwraps_and_decodes(self):
        self.assertEqual(
            self.ie._master_url_from_embed(_WATCH_HTML, 'test'),
            'https://box5.gideo.video/hls/198/directorscut.m3u8?t=TOKEN123')

    def test_master_url_from_embed_absent_returns_none(self):
        self.assertIsNone(self.ie._master_url_from_embed('<html>no player here</html>', 'test'))

    def test_master_url_from_embed_rejects_non_url_livesrc(self):
        html = '<script src="https://app.streamotor.com/player/v2/embed.php?livesrc=not-a-url&x=1"></script>'
        self.assertIsNone(self.ie._master_url_from_embed(html, 'test'))

    def test_angles_for_stream_selected_card(self):
        self.assertEqual(
            self.ie._angles_for_stream(_WATCH_HTML, '38418'),
            [('multi', 'Multi Cam'), ('high', 'High Cam')])

    def test_angles_for_stream_scoped_to_other_id(self):
        # Discovery must key off the requested stream id, not bleed across cards.
        self.assertEqual(
            self.ie._angles_for_stream(_WATCH_HTML, '36272'),
            [('multi', 'Multi Cam'), ('high', 'High Cam')])

    def test_angles_for_stream_unknown_id_empty(self):
        self.assertEqual(self.ie._angles_for_stream(_WATCH_HTML, '99999'), [])

    def test_angles_dedupe_repeated_type(self):
        html = (
            '<a href="/watch/?stream=5&#038;stream-type=multi">Multi Cam</a>'
            '<a href="/watch/?stream=5&#038;stream-type=multi">Multi Cam (dup)</a>'
            '<a href="/watch/?stream=5&#038;stream-type=high">High Cam</a>')
        self.assertEqual(
            self.ie._angles_for_stream(html, '5'),
            [('multi', 'Multi Cam'), ('high', 'High Cam')])

    def test_event_title_from_selected_card(self):
        self.assertEqual(
            self.ie._event_title(_WATCH_HTML),
            'PRELIMS - Fairfield Jr./Sr. H.S., IN')

    def test_event_title_falls_back_to_page_title(self):
        html = '<html><head><title>Watch &#8211; BOX5-TV</title></head><body>no card</body></html>'
        self.assertEqual(self.ie._event_title(html), 'Watch – BOX5-TV')


class TestBox5TVResolveStreams(unittest.TestCase):
    def setUp(self):
        self.ie = Box5TVIE(FakeYDL())

    def _stub_page(self, final_url, html=_WATCH_HTML):
        """Make _download_webpage_handle return our fixture with a chosen final URL."""
        self.ie._download_webpage_handle = lambda *a, **k: (html, _FakeURLH(final_url))

    def _record_resolve_one(self):
        """Replace _resolve_one with a recorder that avoids all network."""
        calls = []

        def fake(video_id, stream_type, label, html=None):
            calls.append((stream_type, label, html is not None))
            return {'id': f'{video_id}-{stream_type}', 'name': label,
                    'manifest_url': f'https://box5.gideo.video/{stream_type}.m3u8?t=x',
                    'is_live': True, 'event_title': 'PRELIMS - Fairfield Jr./Sr. H.S., IN'}
        self.ie._resolve_one = fake
        return calls

    def test_login_redirect_raises_expected(self):
        self._stub_page('https://box5tv.com/livestream/login?_amember_redirect_url=%2Fwatch')
        with self.assertRaises(ExtractorError):
            self.ie._resolve_streams('https://box5tv.com/watch/?stream=38418', '38418')

    def test_pinned_stream_type_resolves_only_that_angle(self):
        self._stub_page('https://box5tv.com/watch/?stream=38418&stream-type=high')
        calls = self._record_resolve_one()
        streams = self.ie._resolve_streams(
            'https://box5tv.com/watch/?stream=38418&stream-type=high', '38418')
        self.assertEqual([c[0] for c in calls], ['high'])
        self.assertEqual([s['name'] for s in streams], ['High Cam'])

    def test_no_stream_type_resolves_all_angles(self):
        self._stub_page('https://box5tv.com/watch/?stream=38418&stream-type=multi')
        calls = self._record_resolve_one()
        streams = self.ie._resolve_streams('https://box5tv.com/watch/?stream=38418', '38418')
        self.assertEqual([c[0] for c in calls], ['multi', 'high'])
        # The page already fetched (multi) is reused; the other angle is fetched fresh.
        self.assertEqual([c[2] for c in calls], [True, False])
        self.assertEqual([s['name'] for s in streams], ['Multi Cam', 'High Cam'])


if __name__ == '__main__':
    unittest.main()
