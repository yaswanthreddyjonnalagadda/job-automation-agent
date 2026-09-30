# Ant Design Select, for tests

`ant-select.bundle.js` is Ant Design's own `Select` (antd 5.29.3) with React
18.3.1, bundled into one file so `tests/test_ant_select.py` can run the agent
against the real widget offline. Dayforce and other portals draw their lists
with it, and a hand-made imitation missed what broke on 23 September: the
list is virtual, and each row holds an empty state marker.

It exposes `window.AntSelectFixture = {React, createRoot, Select}`. The
licences of everything inside (all MIT) are kept at the end of the file.

To rebuild after changing a version in `package.json`:

    npm install
    npm run build
