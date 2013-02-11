#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-02-12 01:37:43 by subi>

from __future__ import unicode_literals

import os

# http://www.python.org/dev/peps/pep-0318/
def singleton(cls):
    instances = {}
    def getinstance():
        if cls not in instances:
            instances[cls] = cls()
        return instances[cls]
    return getinstance

def which(prog):
    paths = [''] if os.path.isabs(prog) else os.environ['PATH'].split(os.pathsep)
    for path in paths:
        fullpath = os.path.join(path, prog)
        if os.access(fullpath, os.X_OK):
          return fullpath
    return None

def fsdecode(stream):
    import sys
    if sys.hexversion < 0x03000000:
        if isinstance(stream, str):
            stream = stream.decode(sys.getfilesystemencoding(),'ignore')
    else:
        if isinstance(stream, bytes):
            stream = os.fsdecode(stream)
    return stream

def find_more_episodes(filepath):
    '''Try to find some following episodes/parts.
    '''
    def translate(s):
        dic = dict(zip('零壹贰叁肆伍陆柒捌玖〇一二三四五六七八九','0123456789'*2))
        return ''.join([dic.get(c,c) for c in s])
    def split_by_int(s):
        res = [(int(x) if x.isdigit() else -len(x)) for x in re.split('(\d+)', translate(s)) if x != '']
        return res
    def strip_to_int(s,prefix):
        # strip the prefix
        if prefix and s.startswith(prefix):
            _,_,s = s.partition(prefix)
        # extract the first int
        val = split_by_int(s)[0]
        return val

    if not os.path.exists(filepath):
        return []

    import re
    pdir, basename = os.path.split(os.path.abspath(filepath))
    _, ext = os.path.splitext(basename)
    # basic candidate filtering
    # 1. extention
    files = [f for f in os.listdir(pdir) if f.endswith(ext)]
    # 2. remove previous episodes
    files.sort(key=split_by_int)
    del files[0:files.index(basename)]

    # not necessary to go further if no candidates
    if len(files) == 1:
        return []

    # find the common prefix
    i_break = 0
    for i in range(min(len(files[0]),len(files[1]))):
        if not files[0][i] == files[1][i]:
           i_break = i
           break
    prefix = files[0][0:i_break]

    # generate the list
    results = []
    for i,f in enumerate(files[1:]):
        if strip_to_int(f,prefix) - strip_to_int(files[i],prefix) == 1:
            results.append(os.path.join(pdir,f))
        else:
            break
    return results

if __name__ == '__main__':
    import sys
    if len(sys.argv) != 1:
        print('\n'.join(find_more_episodes(fsdecode(sys.argv[1]))))
