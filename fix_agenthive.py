import os

with open('beacon_skill/transports/__init__.py', 'r') as f:
    content = f.read()

content = content.replace('"AgentHiveClient",\n    "AgentHiveClient",', '"AgentHiveClient",')

lines = content.split('\n')
seen = set()
newlines = []
for line in lines:
    if line.startswith('from .agenthive import AgentHiveClient'):
        if line in seen: continue
        seen.add(line)
    newlines.append(line)

with open('beacon_skill/transports/__init__.py', 'w') as f:
    f.write('\n'.join(newlines))


with open('beacon_skill/transports/agenthive.py', 'r') as f:
    content = f.read()

content = content.replace('''            try:
                data = resp.json()
                msg = data.get("error", resp.text)
            except Exception:
                msg = resp.text''', '''            try:
                data = resp.json()
                if isinstance(data, dict):
                    msg = data.get("error", resp.text)
                else:
                    msg = resp.text
            except Exception:
                msg = resp.text''')

content = content.replace('    def read_timeline(self, **kwargs) -> Dict[str, Any]:', '    def read_timeline(self, **kwargs) -> Any:')
content = content.replace('    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:', '    def _request(self, method: str, endpoint: str, **kwargs) -> Any:')

with open('beacon_skill/transports/agenthive.py', 'w') as f:
    f.write(content)

