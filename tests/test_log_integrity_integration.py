import ast
from datetime import datetime
import logging
from pathlib import Path
import sys
import pytest
from log_integrity import newest_prior_log, warn_previous_log, finalize_log, inspect_log_file

COMPLETE = b'Session Start\nSession End\nFinal Status: COMPLETE\n'

def test_previous_is_read_only(tmp_path, caplog):
    current=tmp_path/'AutoSpecUploader_current.log'
    assert newest_prior_log(current) is None
    prior=tmp_path/'AutoSpecUploader_old.log'
    for data,status in [(COMPLETE,'complete'),(b'Session Start','incomplete'),(b'\xff','corrupt')]:
        caplog.clear()
        prior.write_bytes(data)
        current.write_bytes(COMPLETE)
        warn_previous_log(current)
        assert prior.read_bytes()==data
        assert current.read_bytes()==COMPLETE
        assert len(caplog.records)==(0 if status=='complete' else 1)
        if status!='complete':
            assert f'integrity={status}' in caplog.text
            assert str(tmp_path) not in caplog.text
            assert 'not a hardware diagnosis' in caplog.text

@pytest.mark.parametrize('outcome', [0, 2, SystemExit(0), SystemExit(1), RuntimeError('startup failed')])
def test_main_always_finalizes(tmp_path,monkeypatch,outcome):
    # Exercise actual entrypoint control flow without creating native Qt windows.
    tree=ast.parse((Path(__file__).parents[1]/'src/AutoSpecUploaderGUI.py').read_text())
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    path=tmp_path/'current.log'
    handler=logging.FileHandler(path,encoding='utf-8')
    root=logging.getLogger()
    monkeypatch.setattr(root,'handlers',[handler])
    monkeypatch.setattr(root,'level',logging.INFO)
    root.manager._clear_cache()
    def run(*args):
        if isinstance(outcome,BaseException):
            raise outcome
        return outcome
    ns=dict(datetime=datetime,setup_logging=lambda:path,logging=logging,_run_application=run,finalize_log=finalize_log,sys=sys)
    exec(compile(ast.Module(body=[main],type_ignores=[]),'entrypoint','exec'),ns)
    with pytest.raises((SystemExit,RuntimeError)):
        ns['main']()
    handler.close()
    assert inspect_log_file(path).status=='complete'
    data=path.read_text()
    expected=0 if outcome==0 or isinstance(outcome,SystemExit) and outcome.code==0 else (outcome if isinstance(outcome,int) else 1)
    assert ('Final Status: COMPLETE' if expected==0 else f'Final Status: ERROR (Application exit code {expected})') in data
    assert data.count('Session End')==1

def test_final_inspection_never_repairs(tmp_path,monkeypatch,capsys):
    path=tmp_path/'damaged.log'
    path.write_bytes(b'\xff')
    monkeypatch.setattr(logging.getLogger(),'handlers',[])
    finalize_log(path,0)
    assert path.read_bytes()==b'\xff'
    assert 'integrity=corrupt' in capsys.readouterr().err

def test_newest_prior(tmp_path):
    import os
    first=tmp_path/'AutoSpecUploader_a.log'; first.write_bytes(COMPLETE)
    second=tmp_path/'AutoSpecUploader_b.log'; second.write_bytes(b'\xff')
    os.utime(first,(1,1)); os.utime(second,(2,2))
    assert newest_prior_log(tmp_path/'AutoSpecUploader_current.log')==second

def test_flush_failure_is_console_only(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    def bad_flush():
        raise OSError('fixture failure')
    path=tmp_path/'current.log'; path.write_bytes(COMPLETE)
    handler=SimpleNamespace(level=100,flush=bad_flush)
    monkeypatch.setattr(logging.getLogger(),'handlers',[handler])
    finalize_log(path,0)
    assert 'handler flush failed' in capsys.readouterr().err
    assert path.read_bytes()==COMPLETE
