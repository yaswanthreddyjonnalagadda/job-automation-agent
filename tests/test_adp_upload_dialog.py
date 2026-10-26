"""ADP document dialogs materialize a shadow input and require Save."""
import pytest

from sites.adp import AdpAdapter
from test_workday_prompt_textbox import agent, browser, page  # noqa: F401
import page_agent


def install_upload(page, receipt=True):
    page.set_content('''<sdf-button role="button" tabindex="0" aria-label="upload Resume"
      style="display:block;width:100px;height:30px">Upload</sdf-button><div id="receipt"></div>
      <script>document.querySelector('sdf-button').onclick=()=>{
        const dialog=document.createElement('div');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-label','Resume Upload');
        dialog.innerHTML='<sdf-file-uploader></sdf-file-uploader><button type="button">Save</button>';
        const host=dialog.querySelector('sdf-file-uploader');host.attachShadow({mode:'open'}).innerHTML='<input type="file" hidden>';
        dialog.querySelector('button').onclick=()=>{
          if(window.receipt)document.querySelector('#receipt').textContent=host.shadowRoot.querySelector('input').files[0].name;
          dialog.remove();
        };
        document.body.append(dialog);
      };</script>''')
    page.evaluate('(value)=>window.receipt=value',receipt)
    return page.get_by_role('button',name='upload Resume')


@pytest.mark.parametrize('receipt',[True,False])
def test_document_upload_commits_dialog_and_requires_attachment_evidence(page,tmp_path,monkeypatch,receipt):
    import sites
    control=install_upload(page,receipt)
    a=agent()
    a.resume_file=tmp_path/'candidate_resume.pdf'
    a.resume_file.write_bytes(b'%PDF-1.4 synthetic upload fixture')
    monkeypatch.setattr(sites,'adapter_for',lambda url:AdpAdapter())
    c=next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.name=='upload Resume')
    assert a.do(page,page_agent.Answer(c.ref,'Resume','upload_resume',a.resume_file.name,'document'),c) is receipt
    assert page.get_by_role('dialog').count() == 0


def test_adp_upload_click_denial_never_opens_a_dialog(page,tmp_path):
    control=install_upload(page)
    path=tmp_path/'resume.pdf';path.write_bytes(b'synthetic')
    from types import SimpleNamespace
    assistant=SimpleNamespace(_click_resiliently=lambda *args,**kwargs:False)
    assert AdpAdapter().upload_attachment(assistant,page,'resume',path) is False
    assert page.get_by_role('dialog').count() == 0
