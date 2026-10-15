#!/usr/bin/env python3
"""Keep node and process metrics exporters under one systemd unit."""

import argparse
import os
import pwd
import signal
import subprocess
import time


parser = argparse.ArgumentParser()
parser.add_argument("--node-binary", required=True)
parser.add_argument("--node-user", default="nobody")
parser.add_argument("--node-listen", required=True)
parser.add_argument("--textfile-dir", required=True)
parser.add_argument("--process-binary", required=True)
parser.add_argument("--process-user", default="process_exporter")
parser.add_argument("--process-config", required=True)
parser.add_argument("--process-listen", required=True)
args = parser.parse_args()

children = []
stopping = False


def account_preexec(user):
    account = pwd.getpwnam(user)
    groups = os.getgrouplist(user, account.pw_gid)

    def drop_privileges():
        os.setgroups(groups)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)

    return drop_privileges


def stop_all(_signum=None, _frame=None):
    global stopping
    stopping = True
    for child in children:
        if child.poll() is None:
            child.terminate()


signal.signal(signal.SIGTERM, stop_all)
signal.signal(signal.SIGINT, stop_all)

commands = [
    (
        [
            args.node_binary,
            "--web.listen-address=" + args.node_listen,
            "--collector.textfile",
            "--collector.textfile.directory=" + args.textfile_dir,
        ],
        args.node_user,
    ),
    (
        [
            args.process_binary,
            "--config.path",
            args.process_config,
            "--web.listen-address=" + args.process_listen,
        ],
        args.process_user,
    ),
]

try:
    for command, user in commands:
        children.append(
            subprocess.Popen(
                command,
                env=os.environ.copy(),
                preexec_fn=account_preexec(user),
            )
        )
except Exception:
    stop_all()
    for child in children:
        child.wait(timeout=20)
    raise

try:
    while True:
        for child in children:
            code = child.poll()
            if code is not None:
                stop_all()
                time.sleep(0.5)
                raise SystemExit(0 if stopping else (code or 1))
        time.sleep(0.5)
finally:
    stop_all()
    for child in children:
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
