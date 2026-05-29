# SPDX-License-Identifier: MIT
from unittest.mock import Mock, patch

import pytest

from beacon_skill.transports.bottube import BoTTubeClient, BoTTubeError


def _mock_response(status_code=200, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.text = text or str(json_data or "")
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no JSON")
    return resp


def test_health_calls_public_endpoint():
    client = BoTTubeClient()
    with patch.object(client.session, "request") as request:
        request.return_value = _mock_response(json_data={"ok": True})
        assert client.health() == {"ok": True}
    request.assert_called_once()
    assert request.call_args.args[:2] == ("GET", "https://bottube.ai/health")


def test_list_videos_passes_limit_and_offset():
    client = BoTTubeClient()
    with patch.object(client.session, "request") as request:
        request.return_value = _mock_response(json_data={"videos": []})
        client.list_videos(limit=3, offset=6, agent="sophia")
    assert request.call_args.args[:2] == ("GET", "https://bottube.ai/api/videos")
    assert request.call_args.kwargs["params"] == {"limit": 3, "offset": 6, "agent": "sophia"}


def test_feed_calls_public_endpoint():
    client = BoTTubeClient()
    with patch.object(client.session, "request") as request:
        request.return_value = _mock_response(json_data={"items": []})
        client.feed(limit=1)
    assert request.call_args.args[:2] == ("GET", "https://bottube.ai/api/feed")
    assert request.call_args.kwargs["params"] == {"limit": 1, "offset": 0}


def test_upload_requires_api_key():
    client = BoTTubeClient()
    with pytest.raises(BoTTubeError, match="API key required"):
        client.upload_video("https://example.com/v.mp4", "Demo")


def test_upload_uses_x_api_key_header():
    client = BoTTubeClient(api_key="bt_test")
    with patch.object(client.session, "request") as request:
        request.return_value = _mock_response(json_data={"ok": True})
        client.upload_video("https://example.com/v.mp4", "Demo", tags=["beacon"])
    assert request.call_args.args[:2] == ("POST", "https://bottube.ai/api/upload")
    assert request.call_args.kwargs["headers"]["X-API-Key"] == "bt_test"
    assert request.call_args.kwargs["json"]["video_url"] == "https://example.com/v.mp4"
