"""Emit only the explicitly sanitized owner receipt, never private task data."""
import json
from ansible.plugins.callback import CallbackBase


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'stdout'
    CALLBACK_NAME = 'control_plane_receipt'

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
            self._display.display('CONTROL_PLANE_FAILURE=' + json.dumps({'status': 'failed', 'action': result._task.action}))

    def v2_runner_on_unreachable(self, result):
        self._display.display('CONTROL_PLANE_FAILURE={"status":"unreachable"}')
