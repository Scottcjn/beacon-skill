with open('tests/test_agenthive.py', 'r') as f:
    content = f.read()

test_addition = '''
    def test_post_message_rate_limit(self):
        client = AgentHiveClient(api_key="test-key")
        client._last_post_ts = time.time()
        with self.assertRaises(AgentHiveError):
            client.post_message("Too fast")

    @patch('requests.Session.request')
    def test_post_message_force(self, mock_request):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "123"}
        mock_request.return_value = mock_resp

        client = AgentHiveClient(api_key="test-key")
        client._last_post_ts = time.time()
        res = client.post_message("Forced", force=True)
        self.assertEqual(res["id"], "123")
'''

if 'test_post_message_rate_limit' not in content:
    content = content.replace('if __name__ == "__main__":', test_addition + '\nif __name__ == "__main__":')

if 'import time' not in content:
    content = content.replace('from unittest.mock import Mock, patch', 'import time\nfrom unittest.mock import Mock, patch')

with open('tests/test_agenthive.py', 'w') as f:
    f.write(content)
