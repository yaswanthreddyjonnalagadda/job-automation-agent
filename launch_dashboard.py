"""One canonical dashboard entry point, using this checkout and its local runtime."""
from pathlib import Path
import os
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent

def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import visible_desktop
    visible_desktop.refuse_if_invisible()
    try:
        with urllib.request.urlopen('http://127.0.0.1:5000/runtime', timeout=2) as response:
            import json
            running = json.load(response)
        if Path(running['directory']).resolve() == ROOT and running['source_id'] == source_id(ROOT):
            print('The current dashboard is already running at http://127.0.0.1:5000')
            return
        raise RuntimeError('A different dashboard runtime is already using port 5000. Stop it before starting this checkout.')
    except urllib.error.HTTPError as exc:
        raise RuntimeError('An unversioned dashboard is already using port 5000. Stop it before starting this checkout.') from exc
    except (OSError, urllib.error.URLError):
        pass
    import web_ui
    print(f'Dashboard runtime: {ROOT} ({web_ui.RUNTIME_INFO["source_id"]})', flush=True)
    web_ui.app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

def source_id(root):
    import hashlib
    digest = hashlib.sha256()
    for name in ('web_ui.py','apply.py','apply_flow.py','page_agent.py','interaction.py'):
        digest.update(name.encode())
        digest.update((root/name).read_bytes())
    return digest.hexdigest()[:16]

if __name__ == '__main__':
    main()
