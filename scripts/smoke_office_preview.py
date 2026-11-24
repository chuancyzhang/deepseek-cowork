"""Opt-in Windows/Office smoke: shared PowerPoint, cancellation and cleanup.

Creates and closes only a hidden sentinel presentation; existing user documents
and the existing PowerPoint process must survive every path.
"""

from qa_support import prepare_qa_directory
QA_ROOT = prepare_qa_directory(__file__)
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

os.environ['COWORK_APP_DATA_DIR']=str(QA_ROOT/'appdata')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import document_preview
from core.process_utils import subprocess_kwargs_no_window
from core.workbench import WorkbenchError


HOLDER = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$existed = @(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count -gt 0
$app = $null; $guard = $null
try {
    $app = [Activator]::CreateInstance([Type]::GetTypeFromProgID('PowerPoint.Application'))
    $guard = $app.Presentations.Add(0)
    $slide = $guard.Slides.Add(1, 1)
    $slide.Shapes.Title.TextFrame.TextRange.Text = 'Unsaved preview guard'
    while ($true) {
        [Console]::Out.WriteLine((@{count=$app.Presentations.Count; saved=[int]$guard.Saved;
            text=$guard.Slides.Item(1).Shapes.Title.TextFrame.TextRange.Text;
            security=[int]$app.AutomationSecurity; alerts=[int]$app.DisplayAlerts} | ConvertTo-Json -Compress))
        [Console]::Out.Flush()
        if ([Console]::In.ReadLine() -ne 'state') { break }
    }
} catch {
    [Console]::Out.WriteLine('Holder error: ' + $_.Exception.Message + ' / ' + $_.InvocationInfo.PositionMessage)
    exit 1
} finally {
    if ($null -ne $guard) { $guard.Saved = -1; $guard.Close() }
    if ($null -ne $app) {
        if (-not $existed -and $app.Presentations.Count -eq 0) { $app.Quit() }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)
    }
}
'''


def wait(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.05)
    raise AssertionError("Office cleanup did not finish")


def run():
    from pptx import Presentation
    from pypdf import PdfReader
    powershell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    holder = subprocess.Popen([powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand",
                               base64.b64encode(HOLDER.encode("utf-16-le")).decode("ascii")],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              **subprocess_kwargs_no_window())
    states = queue.Queue()
    def read():
        for line in iter(holder.stdout.readline, b""):
            states.put(line.decode("utf-8", errors="replace").strip())
    threading.Thread(target=read, daemon=True).start()
    def state(initial=False):
        if not initial:
            holder.stdin.write(b"state\n"); holder.stdin.flush()
        message = states.get(timeout=15)
        try:
            return json.loads(message)
        except ValueError as exc:
            raise AssertionError(f"PowerPoint test holder failed: {message}") from exc
    try:
        baseline = state(initial=True)
        assert baseline["saved"] == 0 and baseline["text"] == "Unsaved preview guard"
        with tempfile.TemporaryDirectory(prefix="cowork-office-smoke-") as temporary:
            root = Path(temporary)
            source = root / "preview.pptx"
            deck = Presentation(); slide = deck.slides.add_slide(deck.slide_layouts[0])
            slide.shapes.title.text = "Office preview regression"
            deck.save(source)
            original = source.read_bytes()
            cache = root / "cache"; cache.mkdir()
            pdf, _ = document_preview.render_office_pdf(source, root=cache, force=True)
            assert Path(pdf).read_bytes().startswith(b"%PDF-")
            pages = PdfReader(pdf).pages
            assert len(pages) == 1 and "Office preview regression" in pages[0].extract_text()
            assert source.read_bytes() == original and state() == baseline
            print("PASS shared PowerPoint render preserves unsaved presentation and application settings", flush=True)
            Path(pdf).unlink()
            broken = root / "broken.pptx"; broken.write_bytes(b"invalid presentation")
            try:
                document_preview.render_office_pdf(broken, root=cache)
                raise AssertionError("invalid presentation was accepted")
            except WorkbenchError as exc:
                assert exc.code == "renderer_failed", exc
            assert state() == baseline and not list(cache.iterdir())
            print("PASS failed render preserves shared PowerPoint and cleans temporary files", flush=True)

            for timing in ("early", "opened", "timeout"):
                cancel = threading.Event()
                marker = root / "opened"
                script = document_preview._OFFICE_SCRIPT
                if timing != "early":
                    # Hold a real, already-opened document at a deterministic
                    # boundary to exercise cancellation while COM is occupied.
                    script = script.replace("        Assert-Active\n        $doc.SaveAs",
                        "        [IO.File]::WriteAllText('" + str(marker).replace("'", "''") + "', 'opened')\n"
                        "        Start-Sleep -Seconds 2\n        Assert-Active\n        $doc.SaveAs")
                def cancel_after_open():
                    wait(marker.exists)
                    cancel.set()
                trigger = threading.Timer(.1, cancel.set) if timing == "early" else threading.Thread(target=cancel_after_open)
                if timing != "timeout": trigger.start()
                try:
                    with patch.object(document_preview, "_OFFICE_SCRIPT", script):
                        document_preview.render_office_pdf(source, root=cache, force=True,
                                                           cancel=cancel, timeout=1.5 if timing == "timeout" else 30)
                    raise AssertionError("cancelled conversion returned success")
                except WorkbenchError as exc:
                    assert exc.code == ("renderer_timeout" if timing == "timeout" else "cancelled"), exc
                finally:
                    if timing != "timeout": trigger.join(15)
                wait(lambda: not list(cache.iterdir()))
                assert state() == baseline and source.read_bytes() == original
                marker.unlink(missing_ok=True)
                print(f"PASS {timing} cancellation/timeout closes only the preview copy", flush=True)
    finally:
        if holder.poll() is None:
            holder.stdin.write(b"close\n"); holder.stdin.flush()
        holder.wait(timeout=15)
        holder.stdin.close(); holder.stdout.close()


def run_edits(paths):
    """Opt-in real-file copy/edit/refresh; originals and old PDFs must survive."""
    import importlib.util
    import hashlib
    from pypdf import PdfReader
    from docx import Document
    from openpyxl import load_workbook
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    spec=importlib.util.spec_from_file_location('office_edit_smoke_tools',ROOT/'skills/documents/impl.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    workspace=QA_ROOT/'editing';workspace.mkdir(parents=True,exist_ok=True)
    # Input fixtures are copied to the isolated workspace before using the same
    # application-owned task-copy handler as an agent. No external write access.
    import shutil
    for index,path in enumerate(paths):
        source=Path(path).resolve();original=source.read_bytes()
        imported=workspace/source.name;shutil.copyfile(source,imported)
        context={'session_id':'edit-smoke','run_id':'edit-'+str(time.time_ns())}
        result=module.document_copy(str(workspace),str(imported),context)
        assert result['ok'],result
        draft=workspace/result['path'];cache=workspace/'pdf';cache.mkdir(exist_ok=True)
        before,old_version=document_preview.render_office_pdf(draft,root=cache,renderer='office')
        old_pdf=Path(before).read_bytes()
        marker='修改验证'
        if draft.suffix=='.docx':
            document=Document(draft);document.paragraphs[0].add_run(' · '+marker)
            document.add_paragraph(marker+'：补充一条交付说明，保留原有正文、表格和样式。');document.save(draft)
            assert marker in Document(draft).paragraphs[0].text
        elif draft.suffix=='.xlsx':
            book=load_workbook(draft);sheet=book.worksheets[0]
            sheet['A3']=marker+'项目';sheet['C3']=7;sheet['D3']=123;sheet['E3']='=C3*D3';book.save(draft);book.close()
            check=load_workbook(draft);assert check.worksheets[0]['E3'].value=='=C3*D3';check.close()
        elif draft.suffix=='.pptx':
            deck=Presentation(draft)
            shape=next(shape for shape in deck.slides[0].shapes if shape.has_text_frame)
            shape.text_frame.paragraphs[0].add_run().text=' · '+marker
            for slide in deck.slides:
                for shape in slide.shapes:
                    if shape.has_chart:
                        data=CategoryChartData();data.categories=['第一阶段','第二阶段','第三阶段'];data.add_series('更新预算',[12,18,24]);shape.chart.replace_data(data)
            deck.save(draft)
            assert any(marker in shape.text for shape in Presentation(draft).slides[0].shapes if shape.has_text_frame)
        else: raise ValueError('Pass DOCX, XLSX and PPTX inputs')
        reused=module.document_copy(str(workspace),str(imported),context)
        assert reused['reused'] and reused['path']==result['path']
        after,new_version=document_preview.render_office_pdf(draft,root=cache,renderer='office')
        pages=PdfReader(after).pages;text=''.join(page.extract_text() for page in pages).replace(' ','')
        assert marker in text,(draft,text[:1000])
        if draft.suffix=='.xlsx': assert '861' in text,text[:1000]
        assert before!=after and old_version!=new_version
        assert Path(before).read_bytes()==old_pdf and source.read_bytes()==original and imported.read_bytes()==original
        output=workspace/('edited-'+draft.suffix[1:]);output.mkdir(exist_ok=True)
        document_preview.render_document_pages(after,output)
        print(json.dumps({'type':draft.suffix,'original_unchanged':True,'draft_reused_without_overwrite':True,
            'preview_version_changed':True,'old_preview_unchanged':True,'pages':len(pages),'pdf':after},ensure_ascii=False),flush=True)


def run_office_guards():
    from docx import Document
    from openpyxl import Workbook
    from core.office_renderers import native_state
    for family,progid in [('word','Word.Application'),('excel','Excel.Application')]:
        root=QA_ROOT/family;root.mkdir(exist_ok=True)
        cache=root/'cache';cache.mkdir(exist_ok=True)
        source=root/('guard.docx' if family=='word' else 'guard.xlsx')
        if family=='word':
            document=Document();document.add_paragraph('Office guard conversion');document.save(source)
            create="$doc=$app.Documents.Add();$doc.Content.Text='UNSAVED GUARD'"
            fields="count=$app.Documents.Count;text=$doc.Content.Text;links=[bool]$app.Options.UpdateLinksAtOpen"
        else:
            book=Workbook();book.active['A1']='Office guard conversion';book.save(source)
            create="$doc=$app.Workbooks.Add();$doc.Worksheets.Item(1).Range('A1').Value2='UNSAVED GUARD'"
            fields="count=$app.Workbooks.Count;text=$doc.Worksheets.Item(1).Range('A1').Value2;events=[bool]$app.EnableEvents"
        script=f"""$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$app=$null;$doc=$null
try{{$app=[Activator]::CreateInstance([Type]::GetTypeFromProgID('{progid}'));{create}
while($true){{[Console]::Out.WriteLine((@{{saved=[bool]$doc.Saved;alerts=[int]$app.DisplayAlerts;security=[int]$app.AutomationSecurity;{fields}}}|ConvertTo-Json -Compress));[Console]::Out.Flush();if([Console]::In.ReadLine() -ne 'state'){{break}}}}
}}catch{{[Console]::Out.WriteLine('GUARD_ERROR: '+$_.Exception.Message+' '+$_.InvocationInfo.PositionMessage)}}finally{{if($doc){{$doc.Close($false);[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc)}};if($app){{[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)}}}}
"""
        holder=subprocess.Popen([str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-NonInteractive','-STA','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,**subprocess_kwargs_no_window())
        states=queue.Queue()
        def reader():
            for line in iter(holder.stdout.readline,b''):states.put(line.decode('utf-8').strip())
        reader_thread=threading.Thread(target=reader,daemon=True);reader_thread.start()
        def read_state(initial=False):
            if not initial: holder.stdin.write(b'state\n');holder.stdin.flush()
            message=states.get(timeout=20)
            try:return json.loads(message)
            except ValueError as exc:raise AssertionError('Office guard holder: '+message) from exc
        try:
            baseline=read_state(True);assert not baseline['saved'] and 'UNSAVED GUARD' in baseline['text']
            document_preview.render_office_pdf(source,root=cache,renderer='office',force=True)
            assert read_state()==baseline
            marker=root/'opened';marker.unlink(missing_ok=True)
            injection="        $stage = 'export'\n        Assert-Active"
            delayed=document_preview._OFFICE_SCRIPT.replace(injection,"        [IO.File]::WriteAllText('"+str(marker).replace("'","''")+"','opened')\n        Start-Sleep -Seconds 2\n"+injection)
            cancel=threading.Event()
            def stop():wait(marker.exists,30);cancel.set()
            trigger=threading.Thread(target=stop);trigger.start()
            try:
                with patch.object(document_preview,'_OFFICE_SCRIPT',delayed):
                    document_preview.render_office_pdf(source,root=cache,renderer='office',force=True,cancel=cancel)
                raise AssertionError('cancel returned success')
            except WorkbenchError as exc: assert exc.code=='cancelled',exc
            finally: trigger.join(30)
            assert native_state(family)['state']=='busy'
            wait(lambda:native_state(family)['state']=='ready',30)
            assert read_state()==baseline
            print('PASS '+family+' render/cancellation preserves unsaved guard and settings; blocked reuse recovers after cleanup',flush=True)
        finally:
            if holder.poll() is None: holder.stdin.write(b'close\n');holder.stdin.flush()
            holder.wait(timeout=20);reader_thread.join(5);holder.stdin.close();holder.stdout.close()


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--edit-documents',nargs='+',type=Path)
    arguments=parser.parse_args()
    if arguments.edit_documents: run_edits(arguments.edit_documents)
    else:
        run()
        run_office_guards()
