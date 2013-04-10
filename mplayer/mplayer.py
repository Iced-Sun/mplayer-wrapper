#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-04-10 13:00:33 by subi>

from __future__ import unicode_literals

from aux import which, fsdecode
from global_setting import *

import os, subprocess, hashlib, json

class MPlayerContext(defaultdict):
    '''Meta information on MPlayer itself. Also include the identify() method.
    '''
    def identify(self, args):
        args = [ self['path'] ] + '-vo null -ao null -frames 0 -identify'.split() + args
        logging.debug('Entering MPlayerContext.identify() ---> Calling subprocess:\n{0}'.format(' '.join(args)))
        return '\n'.join([l for l in fsdecode(subprocess.check_output(args)).splitlines() if l.startswith('ID_')])
    
    def __init__(self, need_context=False):
        super(MPlayerContext,self).__init__(bool)
        
        for p in ['/opt/bin/mplayer','/usr/local/bin/mplayer','/usr/bin/mplayer']:
            if which(p):
                self['path'] = p
                break

        if self['path'] and need_context:
            self.__init_context()

    def __init_context(self):
        logging.debug('Entering MPlayerContext.__init_context()')
        with open(self['path'],'rb') as f:
            self['hash'] = hashlib.md5(f.read()).hexdigest()

        cache_home = os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache'))
        cache_dir = os.path.join(cache_home, 'mplayer-wrapper')
        cache_file = os.path.join(cache_dir, 'info')

        loaded_from_cache = False
        try:
            f = open(cache_file,'r')
        except IOError:
            pass
        else:
            with f:
                try:
                    js = json.load(f)
                except ValueError:
                    pass
                else:
                    if js.get('hash','') == self['hash']: 
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
            with open(cache_file,'w') as f:
                json.dump(self, f)

    def __update_context(self):
        logging.debug('Entering MPlayerContext.__update_context()')
        options = fsdecode(subprocess.Popen([self['path'], '-list-options'], stdout=subprocess.PIPE).communicate()[0]).splitlines()

        if options[-1].startswith('MPlayer2'):
            self['mplayer2'] = True
            option_end = -3
        else:
            # access the item to enforce it when saving
            self['mplayer2'] = False
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
            for l in fsdecode(subprocess.check_output(['ldd',self['path']])).splitlines():
                if 'libass' in l:
                    libass_path = l.split()[2]
            if not libass_path:
                self['ass'] = False
            else:
                if not 'libfontconfig' in fsdecode(subprocess.check_output(['ldd',libass_path])):
                    self['ass'] = False

class MPlayerFifo(object):
    '''MPlayerFifo will maintain a FIFO for IPC with mplayer.
    '''
    def send(self, s):
        if self.args:
            logging.debug('Sending message "{0}" to {1}...'.format(s, self.__path))
            with open(self.__path,'w') as f:
                f.write(s+'\n')
        else:
            logging.info('"{0}" cannot be sent to the non-existing {1}.'.format(s, self.__path))
    
    def __init__(self):
        # can't use __del__() to release resource because MPlayerFifo is used
        # in a daemon thread and hence may result in circular reference.
        import atexit

        self.args = []
        xdg = os.environ['XDG_RUNTIME_DIR']
        if xdg:
            self.__path = os.path.join(xdg, 'mplayer.fifo')
        else:
            import tempfile
            tmpdir = tempfile.mkdtemp()
            atexit.register(lambda d: os.rmdir(d), tmpdir)
            self.__path = os.path.join(tmpdir, 'mplayer.fifo')

        try:
            os.mkfifo(self.__path)
            atexit.register(lambda f: os.unlink(f), self.__path)
            self.args = '-input file={0}'.format(self.__path).split()
        except OSError as e:
            logging.info(e)

class MPlayer(object):
    last_timestamp = 0.0
    last_exit_status = None
    
    def __init__(self, args=[]):
        self.__fifo = MPlayerFifo()
        self.__context = MPlayerContext()
        self.__process = None

        self.__init_args(args)
        self.__set_cmdline_aspect()
        
    def __init_args(self, args):
        self.__global_args = []
        self.__supplement_args = self.__fifo.args

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
                        self.__global_args.append(s)
                elif flag == 2:
                    self.__global_args.append(s)
                    if args:
                        self.__global_args.append(args.pop(0))
            else:
                left_args.append(s)
        args[:] = left_args

        if self.__context['ass']:
            if cmdline_ass:
                self.__supplement_args.append(cmdline_ass)
            else:
                self.__supplement_args.append('-ass')
        else:
            self.__supplement_args.append('-noass')

    def __del__(self):
        logging.debug('Global args:  {0}\n'
                      '  Supplement: {1}'.format(self.__global_args, self.__supplement_args))
        
    def __set_cmdline_aspect(self):
        DAR = None
        if '-aspect' in self.__global_args:
            s = self.__global_args[self.__global_args.index('-aspect')+1]
            if ':' in s:
                x,y = s.split(':')
                DAR = Fraction(int(x),int(y))
            else:
                DAR = Fraction(s)
        elif 'dsize' in self.__global_args:
            # TODO
            pass
        return DAR
        
    def send(self, cmd):
        if self.__process != None:
            self.__fifo.send(cmd)
        
    def play(self, media=None):
        args = [ self.__context['path'] ] + self.__global_args
        if media:
            args += media.mplayer_args()
            if media.is_video():
                args += self.__supplement_args
        logging.debug('\n'+' '.join(args))
        if not config['dry-run']:
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

        logging.debug('Last timestamp: {0}'.format(self.last_timestamp))
        logging.debug('Last exit status: {0}'.format(self.last_exit_status))
        self.__process = None

if __name__ == '__main__':
    import sys
    cntxt = MPlayerContext()
    print(cntxt)
    MPlayer(sys.argv)
#else:
#    mplayer = MPlayer()
