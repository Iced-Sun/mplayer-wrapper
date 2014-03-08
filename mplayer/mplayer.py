#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-08-26 04:14:19 by subi>

from __future__ import unicode_literals

from aux import which, fsencode, fsdecode
from globals import *

import subprocess,hashlib,json
try:
    from subprocess import DEVNULL
except ImportError:
    DEVNULL = open(os.devnull, 'wb')
from collections import defaultdict

class PlayerBackend(object):
    # und, mpv, mp, mp2
    backend = 'und'
    
    @classmethod
    def get_backend(cls):
        return cls.backend

    def __init__(self):
        self.__path = None
        self.__context = defaultlist(bool)
        self.__detect_backend()

    def __detect_backend(self):
        check_list = ['/opt/bin/mpv','/usr/local/bin/mpv','/usr/bin/mpv',
                      '/opt/bin/mplayer','/usr/local/bin/mplayer','/usr/bin/mplayer']

        for p in check_list:
            if which(p):
                self.__path = p
                break

    def __init_context(self):
        # no mplayer binary presents
        if not self.__path:
            return

        cache_file = os.path.join(config.get_cache_dir(), 'info')
        try:
            self.__load_context(cache_file)
        except StandardError as e:
            log_debug('Load context from {} failed because: \n  {}'.format(cache_file, e))

        if not self.__context['option']:
            self.__rebuild_context()
            
            try:
                if not os.path.exists(config.get_cache_dir()):
                    os.mkdir(config.get_cache_dir(),0o700)
                with open(cache_file,'w') as f:
                    json.dump(self, f)
            except StandardError as e:
                log_debug('Save context to {} failed because:\n  {}'.format(cache_file, e))

    def __load_context(self, cache_file):
        with open(self['path'],'rb') as f:
            self['hash'] = hashlib.md5(f.read()).hexdigest()

        with open(cache_file,'r') as f:
            cached_context = defaultdict(bool, json.load(f))
            # load context from cache if /usr/bin/mplayer isn't modified.
            if cached_context['hash'] == self['hash']:
                self['ass'] = cached_context['ass']
                self['mplayer2'] = cached_context['mplayer2']
                self['option'] = defaultdict(int, cached_context['option'])

    def __rebuild_context(self):
        options = fsdecode(subprocess.Popen([self['path'], '-list-options'], stdout=subprocess.PIPE).communicate()[0]).splitlines()

        if options[-1].startswith('MPlayer2'):
            self['mplayer2'] = True
            option_end = -3
        else:
            option_end = -4

        # collect supported options
        self['option'] = defaultdict(int)
        for opt in options[3:option_end]:
            opt = opt.split()
            name = opt[0].split(':') # don't care sub-option
            if self['option'][name[0]]:
                continue
            self['option'][name[0]] = (2 if len(name)==2 or opt[1]!='Flag' else 1)
            
        # handle vf*/af*: mplayer reports option name as vf*/af*, which is a
        # family of options.
        del self['option']['af*']
        del self['option']['vf*']
        for extra in ['af','af-adv','af-add','af-pre','af-del','vf','vf-add','vf-pre','vf-del']:
            self['option'][extra] = 2
        for extra in ['af-clr','vf-clr']:
            self['option'][extra] = 1

        # it's awful to test if ass is supported.
        self['ass'] = True
        if not self['option']['ass']:
            self['ass'] = False
        else:
            libass_path = None
            for l in fsdecode(subprocess.check_output(['ldd',self['path']])).splitlines():
                if 'libass' in l:
                    libass_path = l.split()[2]
            if not libass_path:
                self['ass'] = False
            else:
                if not 'libfontconfig' in fsdecode(subprocess.check_output(['ldd',libass_path])):
                    self['ass'] = False
                
class MPlayerContext(defaultdict):
    def __init__(self):
        super(MPlayerContext,self).__init__(bool)
        
        for p in ['/opt/bin/mplayer','/usr/local/bin/mplayer','/usr/bin/mplayer']:
            if which(p):
                self['path'] = p
                break

    def establish(self):
        # no mplayer binary presents
        if not self['path']:
            return

        cache_file = os.path.join(config.get_cache_dir(), 'info')
        try:
            self.__load_context(cache_file)
        except StandardError as e:
            log_debug('Load context from {} failed because: \n  {}'.format(cache_file, e))

        if not self['option']:
            self.__rebuild_context()
            
            try:
                if not os.path.exists(config.get_cache_dir()):
                    os.mkdir(config.get_cache_dir(),0o700)
                with open(cache_file,'w') as f:
                    json.dump(self, f)
            except StandardError as e:
                log_debug('Save context to {} failed because:\n  {}'.format(cache_file, e))

    def __load_context(self, cache_file):
        with open(self['path'],'rb') as f:
            self['hash'] = hashlib.md5(f.read()).hexdigest()

        with open(cache_file,'r') as f:
            cached_context = defaultdict(bool, json.load(f))
            # load context from cache if /usr/bin/mplayer isn't modified.
            if cached_context['hash'] == self['hash']:
                self['ass'] = cached_context['ass']
                self['mplayer2'] = cached_context['mplayer2']
                self['option'] = defaultdict(int, cached_context['option'])

    def __rebuild_context(self):
        options = fsdecode(subprocess.Popen([self['path'], '-list-options'], stdout=subprocess.PIPE).communicate()[0]).splitlines()

        if options[-1].startswith('MPlayer2'):
            self['mplayer2'] = True
            option_end = -3
        else:
            option_end = -4

        # collect supported options
        self['option'] = defaultdict(int)
        for opt in options[3:option_end]:
            opt = opt.split()
            name = opt[0].split(':') # don't care sub-option
            if self['option'][name[0]]:
                continue
            self['option'][name[0]] = (2 if len(name)==2 or opt[1]!='Flag' else 1)
            
        # handle vf*/af*: mplayer reports option name as vf*/af*, which is a
        # family of options.
        del self['option']['af*']
        del self['option']['vf*']
        for extra in ['af','af-adv','af-add','af-pre','af-del','vf','vf-add','vf-pre','vf-del']:
            self['option'][extra] = 2
        for extra in ['af-clr','vf-clr']:
            self['option'][extra] = 1

        # it's awful to test if ass is supported.
        self['ass'] = True
        if not self['option']['ass']:
            self['ass'] = False
        else:
            libass_path = None
            for l in fsdecode(subprocess.check_output(['ldd',self['path']])).splitlines():
                if 'libass' in l:
                    libass_path = l.split()[2]
            if not libass_path:
                self['ass'] = False
            else:
                if not 'libfontconfig' in fsdecode(subprocess.check_output(['ldd',libass_path])):
                    self['ass'] = False

class MPlayerFifo(object):
    '''MPlayerFifo maintains a FIFO for IPC with mplayer.
    '''
    def send(self, s):
        if self.args:
            log_debug('Sending message "{0}" to {1}...'.format(s, self.__path))
            with open(self.__path,'w') as f:
                f.write(fsencode(s+'\n'))
        else:
            log_info('"{0}" cannot be sent to the non-existing {1}.'.format(s, self.__path))
    
    def __init__(self):
        self.__path = os.path.join(config.get_runtime_dir(), 'mplayer.fifo')
        
        try:
            os.mkfifo(self.__path)
        except OSError as e:
            log_info(e)

        self.args = '-input file={0}'.format(self.__path).split()
            
    def __del__(self):
        try:
            os.unlink(self.__path)
        except StandardError as e:
            log_info(e)

class MPlayer(object):
    last_timestamp = 0.0
    last_exit_status = None
    
    def __init__(self, args=[], minimal=False):
        self.__context = MPlayerContext()
        if not minimal:
            self.__context.establish()
            self.__fifo = MPlayerFifo()
            self.__process = None

            self.__init_args(args)
            self.__set_default_args()
            self.__set_cmdline_aspect()

    def __del__(self):
        # only the non-minimal has hash
        if self.__context['hash']:
            log_debug('Default args:\n'
                      '  Command line: {}\n'
                      '  Extra:        {}'.format(self.__cmdline_args, self.__extra_args))

    def __init_args(self, args):
        self.__cmdline_args = []
        self.__extra_args = self.__fifo.args

        # parse args
        left_args = []
        cmdline_ass = ''
        while args:
            s = args.pop(0)
            if s == '--':
                left_args += args
                args = []
            elif s.startswith('-'):
                flag = self.__context['option'][s.partition('-')[2]]
                if flag == 0:
                    left_args.append(s)
                elif flag == 1:
                    if s == '-ass' or s == '-noass':
                        cmdline_ass = s
                    else:
                        self.__cmdline_args.append(s)
                elif flag == 2:
                    self.__cmdline_args.append(s)
                    if args:
                        self.__cmdline_args.append(args.pop(0))
            else:
                left_args.append(s)
        args[:] = left_args

        if self.__context['ass']:
            if cmdline_ass:
                self.__extra_args.append(cmdline_ass)
            else:
                self.__extra_args.append('-ass')
        else:
            self.__extra_args.append('-noass')

    def __set_default_args(self):
        config.CMDLINE_ARGS=self.__cmdline_args
        config.VIDEO_EXTRA_ARGS=self.__extra_args
        
    def __set_cmdline_aspect(self):
        DAR = None
        from fractions import Fraction
        if '-aspect' in self.__cmdline_args:
            s = self.__cmdline_args[self.__cmdline_args.index('-aspect')+1]
            if ':' in s:
                x,y = s.split(':')
                DAR = Fraction(int(x),int(y))
            else:
                DAR = Fraction(s)
        elif 'dsize' in self.__cmdline_args:
            # TODO
            pass
        log_debug('CMDLINE_ASPECT is set to {}'.format(DAR))
        config.CMDLINE_ASPECT = DAR

    def send(self, cmd):
        if self.__process != None:
            self.__fifo.send(cmd)
        
    def identify(self, args):
        args = [ self.__context['path'] ] + '-vo null -ao null -frames 0 -identify'.split() + args
        log_debug('Entering MPlayerContext.identify() <call subprocess>\n  {}'.format(' '.join(args)))
        output = subprocess.Popen(args,stdout=subprocess.PIPE,stderr=DEVNULL).communicate()[0]
        return '\n'.join([l for l in fsdecode(output).splitlines() if l.startswith('ID_')])
    
    def play(self, args=[]):
        args = [ self.__context['path'] ] + self.__cmdline_args + args
        log_debug('\n'+' '.join(args))
        if not config.DRY_RUN:
            self.__process = subprocess.Popen(args, stdin=sys.stdin, stdout=subprocess.PIPE, stderr=None)
            self.__tee()

    def __tee(self):
        def flush_first_line(fileobj, lines):
            fileobj.write(b''.join(lines.pop(0)))
            fileobj.flush()
            lines.append([])

        f = sys.stdout
        p = self.__process
        
        # cache 5 lines in case of unexpected outputs
        lines = [[] for i in range(5)]
        while True:
            c = p.stdout.read(1)
            if not c:
                break
            lines[4].append(c)

            # carriage return / linefeed
            if c == b'\n':
                flush_first_line(f,lines)
            elif c == b'\r':
                d = p.stdout.read(1)
                if d == b'\n':
                    lines[4].append(b'\n')
                    flush_first_line(f,lines)
                else:
                    flush_first_line(f,lines)
                    lines[4].append(d)
            else:
                pass

        # save info and flush rest outputs
        for l in (b''.join(ll) for ll in lines):
            if l.startswith((b'A:',b'V:')):
                try:
                    self.last_timestamp = float(l[2:9])
                except ValueError:
                    pass
            if l.startswith(b'Exiting...'):
                self.last_exit_status = l[12:len(l)-2]
            f.write(l)
        f.flush()

        log_debug('Last timestamp: {0}'.format(self.last_timestamp))
        log_debug('Last exit status: {0}'.format(self.last_exit_status))
        self.__process = None

if __name__ == '__main__':
    import sys
    cntxt = MPlayerContext()
    print(cntxt)
    MPlayer(sys.argv)
