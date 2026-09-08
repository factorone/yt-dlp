#!/usr/bin/env python3

# Allow direct execution
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from test.helper import FakeYDL
from yt_dlp.extractor.gideo import Box5TVIE
from yt_dlp.utils import ExtractorError


class TestBox5TVValidURL(unittest.TestCase):
    def test_matches_watch_urls(self):
        for url in (
            'https://livestream.box5tv.com/live/12345',
            'https://box5tv.com/video/12345-boa-grand-nationals-2025',
            'https://www.box5tv.com/vod/9999',
            'https://livestream.box5tv.com/watch/some-event-slug',
            'https://box5tv.com/event/spring-2026',
        ):
            self.assertTrue(Box5TVIE.suitable(url), f'should match: {url}')

    def test_rejects_non_watch_urls(self):
        for url in (
            'https://box5tv.com/',
            'https://box5tv.com/cart/',
            'https://box5tv.com/2025-tnmea-all-state-order-here/',
            'https://www.flomarching.com/live/12345',
        ):
            self.assertFalse(Box5TVIE.suitable(url), f'should not match: {url}')

    def test_captures_type_and_id(self):
        mobj = Box5TVIE._match_valid_url('https://livestream.box5tv.com/live/12345')
        self.assertEqual(mobj.group('type'), 'live')
        self.assertEqual(mobj.group('id'), '12345')


class TestBox5TVHelpers(unittest.TestCase):
    def setUp(self):
        self.ie = Box5TVIE(FakeYDL())

    def test_lookback_zero_is_noop(self):
        uri = 'https://cdn.gideo.video/live/stream.m3u8'
        self.assertEqual(self.ie._apply_time_parameters(uri, 0), uri)

    def test_lookback_appends_param_without_query(self):
        uri = 'https://cdn.gideo.video/live/stream.m3u8'
        out = self.ie._apply_time_parameters(uri, 5)
        self.assertTrue(out.startswith(uri + '?start='), out)
        self.assertTrue(out.rsplit('=', 1)[1].isdigit(), out)

    def test_lookback_appends_param_with_existing_query(self):
        uri = 'https://cdn.gideo.video/live/stream.m3u8?token=abc'
        out = self.ie._apply_time_parameters(uri, 5)
        self.assertIn('?token=abc&start=', out)

    def test_lookback_respects_param_name_override(self):
        # DVR param name is unconfirmed; the helper must honor _LOOKBACK_PARAM.
        self.ie._LOOKBACK_PARAM = 'dvr'
        out = self.ie._apply_time_parameters('https://cdn.gideo.video/s.m3u8', 3)
        self.assertIn('?dvr=', out)

    def test_filter_none_returns_all(self):
        streams = [{'id': '1', 'name': 'Main Camera'}, {'id': '2', 'name': 'High Camera'}]
        self.assertEqual(self.ie._filter_stream_by_name(streams, None), streams)

    def test_filter_exact_match_returns_single(self):
        streams = [{'id': '1', 'name': 'Main Camera'}, {'id': '2', 'name': 'High Camera'}]
        result = self.ie._filter_stream_by_name(streams, 'high camera')
        self.assertEqual(result, [{'id': '2', 'name': 'High Camera'}])

    def test_filter_partial_match_returns_subset(self):
        streams = [
            {'id': '1', 'name': 'Main Camera'},
            {'id': '2', 'name': 'High Camera'},
            {'id': '3', 'name': 'Drum Major Cam'},
        ]
        result = self.ie._filter_stream_by_name(streams, 'camera')
        self.assertEqual([s['id'] for s in result], ['1', '2'])

    def test_filter_no_match_returns_all(self):
        streams = [{'id': '1', 'name': 'Main Camera'}]
        self.assertEqual(self.ie._filter_stream_by_name(streams, 'nonexistent'), streams)


class TestBox5TVExtractorArgs(unittest.TestCase):
    def test_defaults(self):
        ie = Box5TVIE(FakeYDL())
        args = ie._get_extractor_args()
        self.assertIsNone(args['stream_name'])
        self.assertEqual(args['delay_minutes'], 0)
        self.assertEqual(args['lookback_minutes'], 0)

    def test_parses_configured_args(self):
        ydl = FakeYDL({'extractor_args': {'box5tv': {
            'stream_name': ['Main Camera'],
            'delay_minutes': ['5'],
            'lookback_minutes': ['10'],
        }}})
        args = Box5TVIE(ydl)._get_extractor_args()
        self.assertEqual(args['stream_name'], 'Main Camera')
        self.assertEqual(args['delay_minutes'], 5)
        self.assertEqual(args['lookback_minutes'], 10)


class TestBox5TVSeams(unittest.TestCase):
    def test_resolve_streams_not_yet_implemented(self):
        # The Streamotor resolution seam must fail loud-but-expected until Phase 1 RE.
        ie = Box5TVIE(FakeYDL())
        with self.assertRaises(ExtractorError):
            ie._resolve_streams('12345', is_live=True)


if __name__ == '__main__':
    unittest.main()
