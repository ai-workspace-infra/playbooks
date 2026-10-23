"""Emit only the explicitly sanitized owner receipt, never private task data."""
import json
import os
import re
from ansible.plugins.callback import CallbackBase


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'stdout'
    CALLBACK_NAME = 'control_plane_receipt'

    @staticmethod
    def _failure(result, status):
        failure = {
            'status': status,
            'stage': os.environ.get('CONTROL_PLANE_STAGE', 'ansible_playbook'),
            'action': str(result._task.action),
        }
        task_name = getattr(result._task, 'name', None)
        if (isinstance(task_name, str) and len(task_name) <= 120
                and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._:/-]*', task_name)
                and '{{' not in task_name and '{%' not in task_name and '{#' not in task_name):
            failure['task_name'] = task_name
        return failure

    def v2_runner_on_ok(self, result):
        if result._result.get('_ansible_no_log') or result._task.no_log:
            return
        message = result._result.get('msg')
        if result._task.name == 'Publish the owner component receipt without process or credential values' \
                and isinstance(message, str) and message.startswith('CONTROL_PLANE_EVIDENCE='):
            receipt = json.loads(message.split('=', 1)[1])
            self._display.display('CONTROL_PLANE_EVIDENCE=' + json.dumps(receipt, sort_keys=True))

    def v2_runner_on_failed(self, result, ignore_errors=False):
        if not ignore_errors:
            self._display.display('CONTROL_PLANE_FAILURE=' + json.dumps(self._failure(result, 'failed'), sort_keys=True))

    def v2_runner_on_unreachable(self, result):
        self._display.display('CONTROL_PLANE_FAILURE=' + json.dumps(self._failure(result, 'unreachable'), sort_keys=True))
