import os
path = 'scp/web_control/browser_session.py'
with open(path, 'r', encoding='utf8') as f:
    content = f.read()

if '_spawned_browsers = []' not in content:
    content = content.replace('import subprocess', 'import subprocess\nimport atexit\n\n_spawned_browsers = []\n\ndef _cleanup_browsers():\n    for p in _spawned_browsers:\n        try:\n            p.terminate()\n        except Exception:\n            pass\natexit.register(_cleanup_browsers)\n')
    
content = content.replace('subprocess.Popen([browser', 'p = subprocess.Popen([browser')
content = content.replace('getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))', 'getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))\n        _spawned_browsers.append(p)')

with open(path, 'w', encoding='utf8') as f:
    f.write(content)
