import urllib.request
import json
import base64

h = {'User-Agent': 'Bot', 'Accept': 'application/vnd.github.v3+json'}
def get_json(url):
    try:
        req = urllib.request.Request(url, headers=h)
        resp = urllib.request.urlopen(req).read().decode('utf-8')
        return json.loads(resp)
    except Exception as e:
        return {"error": str(e)}

print("\n--- PR 156 Review Comments ---")
r156_comments = get_json('https://api.github.com/repos/Scottcjn/beacon-skill/pulls/156/comments')
if isinstance(r156_comments, list):
    for c in r156_comments:
        print(f"File: {c.get('path')} | Line: {c.get('line')}\nComment: {c.get('body')}\n")

