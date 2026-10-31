#!/usr/bin/env python3
import re
import sys
text = open(sys.argv[-1]).read()
sys.exit(0 if re.fullmatch(r"[a-z_][a-z0-9_-]* ALL=\(ALL\) NOPASSWD: ALL\n", text) else 1)
