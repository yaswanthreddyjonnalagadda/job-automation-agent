"""One canonical dashboard entry point, using this checkout and its local runtime."""
from pathlib import Path
import os
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent

def main():
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
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import web_ui
    print(f'Dashboard runtime: {ROOT} ({web_ui.RUNTIME_INFO["source_id"]})', flush=True)
    web_ui.app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

def source_id(root):
    import hashlib
    digest = hashlib.sha256()
    sources = list(root.glob('*.py'))
    for package in ('sites', 'tracking', 'confirmation'):
        sources.extend((root/package).glob('*.py'))
    for path in sorted(sources):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]

if __name__ == '__main__':
    main()
