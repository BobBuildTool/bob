#!/usr/bin/env python3

import gzip
import io
import json
import sys

with gzip.open(sys.argv[1], 'rb') as gzf:
    tree = json.load(io.TextIOWrapper(gzf, encoding='utf8'))
    print(tree["artifact"]["files"][sys.argv[2]])
