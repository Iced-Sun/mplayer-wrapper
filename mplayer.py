#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-02-09 16:58:33 by subi>
#
# mplayer-wrapper is an MPlayer frontend, trying to be a transparent interface.
# It is convenient to rename the script to "mplayer" and place it in your $PATH
# (don't overwrite the real MPlayer); you would not even notice its existence.

# TODO:
# * data persistance for
#    i)   resume last played position
#    ii)  remember last settings (volume/hue/contrast etc.)
#    iii) dedicated dir for subtitles
#    iv)  sub_delay info from shooter
# * remember last volume/hue/contrast for continuous playing (don't need data
#   persistance)
# * shooter sometimes return a false subtitle with the same time length. find a
#   cure. (using zenity, pygtk, or both?)
# * xset s off
# * "not compiled in option"
# * detect the language in embedded subtitles, which is guaranteed to be utf8
# * use ffprobe for better(?) metainfo detection?

from __future__ import unicode_literals

from mplayer.aux import singleton,which

import logging
import os,sys
import subprocess, threading, time
import hashlib
import re
import io
import json
from collections import defaultdict

### Application classes
class Application(object):
    '''The application class will:
    1. parse command line arguments
    2. provide run() method to accomplish its role
    '''
    def __init__(self, args):
        logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)

        if '--debug' in args:
            logging.root.setLevel(logging.DEBUG)
            args.remove('--debug')
            config['debug'] = True
        if '--dry-run' in args:
            logging.root.setLevel(logging.DEBUG)
            args.remove('--dry-run')
            config['dry-run'] = True
            config['debug'] = True

class Identifier(Application):
    def __init__(self,args):
        super(Identifier, self).__init__(args)
        self.args = args

    def run(self):
        print MPlayer().identify(self.args)
        
class Player(Application):
    def __init__(self, args):
        super(Player, self).__init__(args)
        self.args = defaultdict(list)

        self.mplayer = MPlayer()
        self.mplayer.pick_args(args)

        # parse the left args
        while args:
            s = args.pop(0)
            if s == '--':
                self.args['file'] += args
                args = []
            elif s.startswith('-'):
                self.args['invalid'].append(s)
            else:
                self.args['file'].append(s)

        self.playlist = self.args['file'][:]
        if self.args['invalid']:
            logging.info('Unknown option(s) "' + ' '.join(self.args['invalid']) + '" are ignored.')

    def run(self):
        if not self.playlist:
            self.mplayer.play()
        else:
            # Use a separate thread to reduce the noticeable lag when finding
            # episodes in a big directory.
            playlist_lock = threading.Lock()
            playlist_thread = threading.Thread(target=self.generate_playlist, args=(playlist_lock,))
            playlist_thread.daemon = True
            playlist_thread.start()
            
            while self.playlist:
                with playlist_lock:
                    f = self.playlist.pop(0)
                m = Media(f)
                m.prepare_mplayer_args()
                watch_thread = threading.Thread(target=self.watch, args=(m,))
                watch_thread.daemon = True
                watch_thread.start()

                self.mplayer.play(m)
                if self.mplayer.last_exit_status == 'Quit':
                    break

                playlist_thread.join()

    def watch(self, media):
        if media.is_video():
            media.fetch_remote_subtitles_and_save(load_in_mplayer=True)
            
    def generate_playlist(self, lock):
        from mplayer.aux import find_more_episodes
        time.sleep(1.5)
        with lock:
            self.playlist += find_more_episodes(self.args['file'][-1])
            
class Fetcher(Application):
    def __init__(self, args):
        self.savedir = None
        self.files = []
        
        super(Fetcher,self).__init__(args)
        for arg in args:
            if arg.startswith('--savedir'):
                self.savedir = arg.split('=')[1]
            else:
                self.files += [arg]

    def run(self):
        for f in self.files:
            Media(f).fetch_remote_subtitles_and_save(sub_savedir=self.savedir)
            
### Main modules
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
        # don't use __del__() to release resource because MPlayerFifo tends to
        # be used in a daemon thread and hence may result in circular reference.
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

class Media(object):
    def is_video(self):
        return self.__info['video']
    
    def mplayer_args(self):
        return self.args

    def prepare_mplayer_args(self):
        # collect media info by midentify
        self.__raw_info['mplayer'] = defaultdict(list)
        raw = self.__raw_info['mplayer']

        for l in MPlayer().identify(self.args).splitlines():
            k,_,v = l.partition('=')
            raw[k].append(v)
            
        info = self.__info
        if raw['ID_VIDEO_ID']:
            from mplayer.dim import apply_geometry_fix
            info['video'] = True

            # preparation
            w = int(raw['ID_VIDEO_WIDTH'][0])
            h = int(raw['ID_VIDEO_HEIGHT'][0])
            DAR_advice = float(raw['ID_VIDEO_ASPECT'][0]) if raw['ID_VIDEO_ASPECT'] else 0.0
            DAR_force = MPlayer().get_cmdline_aspect()

            # record info
            info['width'], info['heigth'] = w, h
            info['DAR'], info['PAR'], args = apply_geometry_fix(w,h,DAR_advice,DAR_force)
            for item in args:
                self.add_arg(item)
                
            # subtitles
            self.parse_local_subtitles()

    def parse_local_subtitles(self):
        info = self.__info
        raw = self.__raw_info['mplayer']
        
        info['subtitle'] = defaultdict(bool)
        if raw['ID_SUBTITLE_ID']:
            # TODO: extract subtitles and combine to a bi-lingual sub
            # ffmpeg -i Seinfeld.2x01.The_Ex-Girlfriend.xvid-TLF.mkv -vn -an -scodec srt sub.srt
            info['subtitle']['embed'] = []
            for i in raw['ID_SUBTITLE_ID']:
                info['subtitle']['embed'] += raw['ID_SID_{0}_LANG'.format(i)]
        if raw['ID_FILE_SUB_ID']:
            info['subtitle']['external'] = raw['ID_FILE_SUB_FILENAME']
            logging.debug('Converting the external subtitles to UTF-8...')
            for subfile in raw['ID_FILE_SUB_FILENAME']:
                # open in binary mode because we don't know the encoding
                with open(subfile,'r+b') as f:
                    s = f.read()
                    enc,_,s = guess_locale_and_convert(s)
                    if not enc in ['utf_8','ascii']:
                        f.seek(0)
                        f.write(s)
            self.add_arg('-subcp utf8')
        if raw['ID_VOBSUB_ID']:
            info['subtitle']['vobsub'] = True
            unrar = which('unrar')
            if unrar:
                self.add_arg('-unrarexec {0}'.format(unrar))
        
    def fetch_remote_subtitles_and_save(self, sub_savedir=None, load_in_mplayer=False):
        info = self.__info

        if not info['subtitle']:
            # if parse_local_subtitles() not done
            info['subtitle'] = defaultdict(bool)

        if info['subtitle']['embed'] and set(info['subtitle']['embed'])&{'chs','cht','chn','chi','zh','tw','hk'}:
            # have Chinese text subtitles
            pass
        elif info['subtitle']['external']:
            # TODO: language?
            pass
        else:
            from mplayer.sub import fetch_subtitle
            info['subtitle']['remote'] = fetch_subtitle(info['abspath'], info['shash'], sub_savedir, config['dry-run'])
            if load_in_mplayer:
                for s in info['subtitle']['remote']:
                    MPlayer().send('sub_load "{0}"'.format(s['path']))
                MPlayer().send('sub_file 0')
            
    def add_arg(self,arg,force=False):
        never_overwritten = ['-vf-pre','-vf-add']
        arg = arg.split()
        if force or arg[0] in never_overwritten or not arg[0] in self.args:
            self.args += arg

    def __init__(self,path):
        self.args = [path]
        
        self.__info = defaultdict(bool)
        self.__raw_info = defaultdict(bool)
        self.__info['path'] = path

        if not os.path.exists(path):
            return

        # basic info
        info = self.__info
        info['abspath'] = os.path.abspath(info['path'])
        sz = os.path.getsize(info['path'])
        if sz>8192:
            with open(info['path'],'rb') as f:
                info['shash'] = ';'.join([(f.seek(s), hashlib.md5(f.read(4096)).hexdigest())[1] for s in (lambda l:[4096, l/3*2, l/3, l-8192])(sz)])

    def __del__(self):
        if not config['debug']:
            return
        info = self.__info
        log_items = ['Media info:',
                     '  Fullpath:  {}'.format(info['abspath']),
                     '  Hash:      {}'.format(info['shash'])]
        if info['video']:
            log_items.append('  Dimension: {}x{} [PAR {} DAR {}]'
                             .format(info['width'], info['height'],
                                     '{0.numerator}:{0.denominator}'.format(info['PAR']),
                                     '{0.numerator}:{0.denominator}'.format(info['DAR'])))
        logging.debug('\n'.join(log_items))

@singleton
class MPlayer(object):
    last_timestamp = 0.0
    last_exit_status = None
    
    def __init__(self, args=[]):
        self.__fifo = MPlayerFifo()

        self.__global_args = []
        self.__supplement_args = self.__fifo.args

        from mplayer.mplayer import MPlayerContext
        self.__context = MPlayerContext()

        self.__process = None
        
    def pick_args(self, args):
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
        
    def get_cmdline_aspect(self):
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
        
    def identify(self, args):
        args = [ self.__context['path'] ] + '-vo null -ao null -frames 0 -identify'.split() + args
        if config['debug']:
            logging.debug('Identifying:\n{0}'.format(' '.join(args)))
        return b'\n'.join([l for l in subprocess.check_output(args).splitlines() if l.startswith(b'ID_')]).decode(config['enc'],'ignore')
    
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
    if sys.hexversion < 0x02070000:
        print 'Please run the script with python>=2.7'
    else:
        config = defaultdict(bool)
        config['enc'] = sys.getfilesystemencoding()
        
        args = [x.decode(config['enc']) for x in sys.argv]
        name = os.path.basename(args.pop(0))
        if 'mplayer' in name:
            app = Player
        elif 'mfetch' in name:
            app = Fetcher
        elif 'midentify' in name:
            app = Identifier
        else:
            app = Application

        app(args).run()
        
