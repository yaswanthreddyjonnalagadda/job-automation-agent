"""A frame disappearing during guard activation must not crash or weaken it."""
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError
from submission_guard import SubmissionGuardV0


@pytest.mark.parametrize('detached', [True,False])
def test_activation_ignores_only_a_confirmed_detached_frame(detached):
    class Page:
        def on(self,*args):
            return None
    page=Page()
    disappearing=SimpleNamespace(is_detached=lambda:detached)
    live=object()
    page.frames=[disappearing,live]
    guard=SubmissionGuardV0.__new__(SubmissionGuardV0)
    guard._active_pages=set()
    activated=[]
    def activate(frame):
        if frame is disappearing:
            raise PlaywrightError('Frame was detached')
        activated.append(frame)
    guard._activate_frame=activate
    if detached:
        guard.activate(page)
        assert activated == [live]
    else:
        with pytest.raises(PlaywrightError):
            guard.activate(page)
        assert activated == []
