#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-02-09 16:57:04 by subi>

from __future__ import unicode_literals

from aux import which

import os, subprocess, hashlib, json
from collections import defaultdict

class MPlayerContext(defaultdict):
    def __init__(self):
        super(MPlayerContext,self).__init__(bool)
        
        for p in ['/opt/bin/mplayer','/usr/local/bin/mplayer','/usr/bin/mplayer']:
            if which(p):
                self['path'] = p
                break

        if self['path']:
            self.__init_context()

    def __update_context(self):
        options = subprocess.Popen([self['path'], '-list-options'], stdout=subprocess.PIPE).communicate()[0].splitlines()
        if options[-1].startswith('MPlayer2'):
            self['mplayer2'] = True
            option_end = -3
        else:
            option_end = -4

        self['option'] = defaultdict(int)
        for opt in options[3:option_end]:
            opt = opt.split()
            name = opt[0].split(':') # don't care sub-option
            if self['option'][name[0]]:
                continue
            self['option'][name[0]] = (2 if len(name)==2 or opt[1]!='Flag' else 1)
            
        # handle vf* af*:
        # mplayer reports option name as vf*, which is a family of options.
        del self['option']['af*']
        del self['option']['vf*']
        for extra in ['af','af-adv','af-add','af-pre','af-del','vf','vf-add','vf-pre','vf-del']:
            self['option'][extra] = 2
        for extra in ['af-clr','vf-clr']:
            self['option'][extra] = 1

        # ASS facility
        self['ass'] = True
        if not self['option']['ass']:
            self['ass'] = False
        else:
            libass_path = None
            for l in subprocess.check_output(['ldd',self['path']]).splitlines():
                if 'libass' in l:
                    libass_path = l.split()[2]
            if not libass_path:
                self['ass'] = False
            else:
                if not 'libfontconfig' in subprocess.check_output(['ldd',libass_path]):
                    self['ass'] = False

    def __init_context(self):
        with open(self['path'],'rb') as f:
            self['hash'] = hashlib.md5(f.read()).hexdigest()

        cache_home = os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache'))
        cache_dir = os.path.join(cache_home, 'mplayer-wrapper')
        cache_file = os.path.join(cache_dir, 'info')

        loaded_from_cache = False
        try:
            f = open(cache_file,'rb')
        except IOError:
            pass
        else:
            with f:
                try:
                    js = json.load(f)
                except ValueError:
                    pass
                else:
                    if js['hash'] == self['hash']:
                        self['ass'] = js['ass']
                        self['mplayer2'] = js['mplayer2']
                        self['option'] = defaultdict(int,js['option'])

                        loaded_from_cache = True

        # rebuild cache if there no one or /usr/bin/mplayer changes
        if not loaded_from_cache:
            self.__update_context()
            
            # save to disk
            if not os.path.exists(cache_dir):
                os.mkdir(cache_dir,0o700)
            with open(cache_file,'wb') as f:
                json.dump(self, f)

if __name__ == '__main__':
    cntxt = MPlayerContext()
    print(cntxt)
