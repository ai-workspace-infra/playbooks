#!/usr/bin/env python3
"""Produce a bounded public projection, Markdown, JUnit and checksums; no raw payloads."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

NAMES = ['DNS 四条记录与切流目标','TLS/路由/首页/登录页','服务健康','登录业务校验','Session/Billing 鉴权',
         'Schema 与 dirty','业务数据与迁移基线','订阅外键','身份字段与批准例外','实时流量与入账',
         'Agent 心跳覆盖率','区域池 API/页面','Flux/ESO/漂移','Vault Raft/KV','Grafana/面板/告警',
         '真实登录业务页面','三轮幂等','隔离恢复与回滚','干净重建','迁移溯源','单写者交接','切流与回滚','证据契约']
SAFE_OBSERVATION = {'agent_count','bytes_15m','dirty','fresh_agents','orphan_subscriptions','plans',
                    'positive_buckets_15m','quota','schema','subscriptions','users','all_ready','resource_count'}
STATUSES = {'PASS','FAIL','PARTIAL','NOT_RUN','SKIPPED','UNKNOWN'}


def write_bundle(raw, output, meta):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', meta['run_id']):
        raise ValueError('invalid execution ID')
    if not re.fullmatch('[a-f0-9]{40}', meta['owner_sha']):
        raise ValueError('full owner SHA required')
    if meta['environment'] not in {'sit','uat','prod'} or not re.fullmatch(r'[a-zA-Z0-9_.-]+',meta['host_id']):
        raise ValueError('invalid target identity')
    source_checks = raw.get('checks', {}) if isinstance(raw, dict) else {}
    if not isinstance(source_checks, dict): source_checks = {}
    checks = []
    for n, name in enumerate(NAMES, 1):
        key = f'V5-{n:02}'
        value = source_checks.get(key, {})
        if not isinstance(value, dict): value = {}
        status = value.get('status', 'NOT_RUN')
        if type(status) is not str or status not in STATUSES: status = 'UNKNOWN'
        values = value.get('observation', {})
        if not isinstance(values, dict): values = {}
        safe = {}
        for field in SAFE_OBSERVATION:
            v = values.get(field)
            if type(v) in (int, bool) or field == 'schema' and type(v) is str and re.fullmatch('[0-9]+',v):
                safe[field] = v
        checks.append({'id':key,'name':name,'status':status,'observation':safe})
    result = 'PASS' if all(x['status']=='PASS' for x in checks) and raw.get('result')=='PASS' else 'FAIL'
    sampled = raw.get('sampled_at')
    try:
        parsed = datetime.fromisoformat(sampled.replace('Z','+00:00'))
        if parsed.tzinfo is None: raise ValueError()
        sampled = parsed.astimezone(timezone.utc).isoformat()
    except (AttributeError, ValueError): sampled = None
    gitops_sha = raw.get('gitops_sha', '')
    if not isinstance(gitops_sha,str) or not re.fullmatch('[a-f0-9]{40}',gitops_sha): gitops_sha = None
    bundle = {'schema_version':'v5-report/v1','execution_mode':'ssh-playbook-role',**meta,
              'generated_at':datetime.now(timezone.utc).isoformat(),'sampled_at':sampled,
              'scope_verified':raw.get('environment') == meta['environment'] and bool(source_checks),
              'gitops_sha':gitops_sha,'result':result,'checks':checks,
              'limitations':['Observation only; not a GitHub dispatch receipt.',
                             'Non-PASS required checks block acceptance; no historical PASS inherited.']}
    output = Path(output)
    if output.is_symlink(): raise ValueError('symlink output forbidden')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Refuse to overwrite another run or reuse old evidence silently.
    if any(output.iterdir()): raise ValueError('report directory must be new and empty')
    os.chmod(output,0o700)
    json_path = output/'evidence.json'
    json_path.write_text(json.dumps(bundle,ensure_ascii=False,indent=2)+'\n')
    lines=['# V5 K3s 自动实测报告','',f"总体结果：**{result}**。非 PASS 的必需检查均阻断放行。",'',
           f"- 执行 ID：`{meta['run_id']}`",f"- 请求环境 / 主机：`{meta['environment']}` / `{meta['host_id']}`",
           f"- Playbooks SHA：`{meta['owner_sha']}`",f"- GitOps SHA：`{gitops_sha or 'NOT_VERIFIED'}`",
           f"- 采样时间（UTC）：`{sampled or 'NOT_AVAILABLE'}`", f"- 目标范围核实：{'已通过' if bundle['scope_verified'] else '未通过或无法采样'}",'',
           '| 编号 | 检查 | 结果 | 脱敏事实 |','|---|---|---|---|']
    fields={'V5-06':('schema','dirty'),'V5-07':('users','plans','subscriptions','quota'),
            'V5-08':('orphan_subscriptions',),'V5-10':('positive_buckets_15m','bytes_15m'),
            'V5-11':('agent_count','fresh_agents'),'V5-13':('resource_count','all_ready')}
    for c in checks:
        facts=', '.join(f"{key}={c['observation'][key]}" for key in fields.get(c['id'],()) if key in c['observation']) or '—'
        lines.append(f"| {c['id']} | {c['name']} | {c['status']} | {facts} |")
    lines += ['', '## 证据与阅读边界','',
              '本报告由同一 Playbook/Role 自动生成，供人和 AI 阅读。`evidence.json` 为脱敏结构化事实，`junit.xml` 为业务验收结果；`manifest.json` 绑定文件摘要。',
              'PARTIAL、NOT_RUN、SKIPPED、UNKNOWN 在 JUnit 中均记为失败，不能作为放行证据。用例有效性测试通过不等于业务验收通过。',
              '本包为 SSH 观察报告，不能冒充 GitHub dispatch 回执或证明迁移、单写者交接、切流、恢复已完成。',
              '未验证项目继续保持 NOT_RUN，历史 PASS 不自动继承。']
    (output/'report.zh.md').write_text('\n'.join(lines)+'\n')
    failed = sum(c['status']!='PASS' for c in checks)
    suite = ET.Element('testsuite',name='V5 business acceptance',tests='23',failures=str(failed),errors='0',skipped='0')
    for c in checks:
        case = ET.SubElement(suite,'testcase',classname='v5.acceptance',name=c['id'])
        if c['status']!='PASS': ET.SubElement(case,'failure',type=c['status'],message='Required check is not PASS')
    ET.ElementTree(suite).write(output/'junit.xml',encoding='utf-8',xml_declaration=True)
    manifest = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for p in output.iterdir():p.chmod(0o600)
    return bundle


def main():
    parser = argparse.ArgumentParser()
    for name in ('output','run-id','owner-sha','environment','host-id'):parser.add_argument('--'+name,required=True)
    args = parser.parse_args()
    try:
        raw=json.load(sys.stdin)
        if not isinstance(raw,dict):raise ValueError()
        result=write_bundle(raw,args.output,{'run_id':args.run_id,'owner_sha':args.owner_sha,'environment':args.environment,'host_id':args.host_id})
    except (ValueError,TypeError,OSError):parser.exit(1,'Report generation failed; invalid input or unsafe output.\n')
    print(json.dumps({'result':result['result'],'report':'report.zh.md'}))


if __name__=='__main__':main()
