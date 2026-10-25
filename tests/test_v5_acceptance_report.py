import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('report',ROOT/'roles/v5_readonly_acceptance/files/report.py')
report=importlib.util.module_from_spec(spec);spec.loader.exec_module(report)
META=dict(run_id='manual-1',owner_sha='a'*40,environment='prod',host_id='instance-1')


def test_report_excludes_arbitrary_payload_and_treats_nonpass_as_failure(tmp_path):
    raw=dict(result='PASS',checks={'V5-06':dict(status='PASS',observation={'schema':'2026102002','password':'synthetic-secret'}),
                                 'V5-11':dict(status='SKIPPED')},raw_token='synthetic-secret')
    result=report.write_bundle(raw,tmp_path/'report',META)
    assert result['result']=='FAIL'
    assert 'synthetic-secret' not in ''.join(p.read_text() for p in (tmp_path/'report').iterdir())
    suite=ET.parse(tmp_path/'report/junit.xml').getroot()
    assert suite.attrib['tests']=='23' and suite.attrib['failures']=='22' and suite.attrib['skipped']=='0'
    assert (tmp_path/'report/report.zh.md').exists()
    assert len(json.loads((tmp_path/'report/manifest.json').read_text()))==3


def test_empty_failure_still_generates_all_required_not_run(tmp_path):
    result=report.write_bundle(dict(result='FAIL',checks={}),tmp_path/'report',META)
    assert len(result['checks'])==23
    assert all(c['status']=='NOT_RUN' for c in result['checks'])
    assert not result['scope_verified']


def test_reuse_of_report_directory_rejected(tmp_path):
    report.write_bundle(dict(result='FAIL'),tmp_path/'report',META)
    with pytest.raises(ValueError):report.write_bundle(dict(result='PASS'),tmp_path/'report',META)


def test_all_pass_only_when_complete_and_consistent(tmp_path):
    raw=dict(result='FAIL',checks={f'V5-{n:02}':dict(status='PASS') for n in range(1,24)})
    assert report.write_bundle(raw,tmp_path/'report',META)['result']=='FAIL'
