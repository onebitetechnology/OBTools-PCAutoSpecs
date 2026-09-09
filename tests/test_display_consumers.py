from types import SimpleNamespace as NS
from panels import SystemInfoPanel
from report_formatter import ReportFormatter


def render(specs):
    rows=[]
    sec=NS(clear_dynamic=lambda:None,add_group_gap=lambda:None,
           add_info_row=lambda label,value,**kw:rows.append((label,value)))
    SystemInfoPanel._update_monitors(NS(_sec_monitors=sec),specs)
    return rows, ' '.join(ReportFormatter()._format_display_config(specs))


def test_structured_roles_and_exact_size():
    specs=dict(SystemType='Laptop',Display='STALE DUPLICATE',
        PanelDetails=dict(model_code='CMN15F5',size_inches=16,size_display='16.3 in measured (16-inch class)',size_cm_h=36,size_cm_v=21),
        DisplayDetails=[dict(role='internal',name='INTERNAL',model='CMN15F5'),
                        dict(role='external',name='DELL HDMI'),dict(role='unknown',name='AMBIGUOUS')])
    rows,html=render(specs)
    assert sum(label=='Built-in Panel' for label,_ in rows)==1
    assert ('Panel Model','CMN15F5') in rows
    assert ('External 1','DELL HDMI') in rows
    assert ('Unclassified 1','AMBIGUOUS') in rows
    assert '16.3 in measured (16-inch class)' in html
    assert '36cm × 21cm' in html
    assert html.count('DELL HDMI')==html.count('AMBIGUOUS')==1
    assert 'External Displays:' in html and 'Unclassified Displays:' in html
    assert 'STALE DUPLICATE' not in html


def test_internal_evidence_survives_missing_panel_query():
    rows,html=render(dict(SystemType='Desktop',PanelDetails=None,
        DisplayDetails=[dict(role='internal',name='CMN15F5',model='CMN15F5',connection_type='DisplayPort (Embedded)')]))
    assert ('Built-in Panel','CMN15F5') in rows
    assert 'CMN15F5' in html and 'Built-in LCD Panel' in html
    assert 'External Displays' not in html


def test_unknown_only_and_legacy():
    rows,html=render(dict(DisplayDetails=[dict(role='unknown',name='Unknown monitor')]))
    assert rows==[('Unclassified 1','Unknown monitor')]
    assert 'External Displays' not in html
    rows,html=render(dict(Display='Legacy Dell',SystemType='Desktop'))
    assert rows==[('Display 1','Legacy Dell')]
    assert 'Legacy Dell' in html
