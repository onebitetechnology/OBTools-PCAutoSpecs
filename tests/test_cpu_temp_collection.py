from types import SimpleNamespace
import multiprocessing
import time
import pytest
from diagnostics import advanced_health


@pytest.mark.parametrize('temps,cancel_at,status,ramp,load', [
    ([55,72,95,74,75,74,75],None,'ok',95,75),
    ([55,100],None,'ok',100,None),
    ([55],1,'cancelled',55,None),
    ([55,72,95,74],4,'cancelled',95,74),
    ([None]*7,None,'ok',None,None),
    ([55,72,95,100],None,'ok',95,100),
])
def test_collection_preserves_phases(monkeypatch,temps,cancel_at,status,ramp,load):
    state={'time':0,'terminated':False,'closed':False,'finished':False}
    monkeypatch.setattr(time,'monotonic',lambda:state['time'])
    monkeypatch.setattr(time,'sleep',lambda seconds:state.update(time=state['time']+seconds))
    monkeypatch.setattr(multiprocessing,'cpu_count',lambda:1)
    queue=SimpleNamespace(put=lambda v:None,put_nowait=lambda v:None,
        close=lambda:state.update(closed=True))
    worker=SimpleNamespace(start=lambda:None,is_alive=lambda:not state['terminated'],
        terminate=lambda:state.update(terminated=True),join=lambda **kw:None)
    monkeypatch.setattr(multiprocessing,'Queue',lambda:queue)
    monkeypatch.setattr(multiprocessing,'Process',lambda **kw:worker)
    values=iter(temps)
    monkeypatch.setattr(advanced_health,'_collect_cpu_temp_info',
        lambda:dict(temp_c=next(values),sensor='fixture'))
    logs=[]
    result=advanced_health.collect_cpu_temp_under_load(duration_sec=4,ramp_sec=3,
        cancel_requested_callback=lambda:cancel_at is not None and state['time']>=cancel_at,
        finished_callback=lambda:state.update(finished=True),log_callback=logs.append)
    assert result['status']==status
    assert result['ramp_peak_temp_c']==ramp
    assert result['load_peak_temp_c']==load
    assert result['throttling_detected'] is False
    assert result['peak_temp_c']==result['overall_peak_temp_c']
    assert state['terminated'] and state['closed'] and state['finished']
    if ramp==95 and load==75:
        assert result['load_median_temp_c']==74.5
        assert result['throttling_evidence']=='suspected'
        assert 'possible throttling' in ''.join(logs)
    if ramp==100:
        assert result['aborted']
        assert result['samples']==[]
