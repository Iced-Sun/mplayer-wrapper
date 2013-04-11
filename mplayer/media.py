#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-04-11 23:52:38 by subi>

from __future__ import unicode_literals
import hashlib
from collections import defaultdict

from global_setting import *
import subtitle

class Media(object):
    def play(self):
        self.prepare_mplayer_args()
        singleton.mplayer.play(self.args)

    def fetch_remote_subtitles(self, sub_savedir=None):
        info = self.__info
        subtitle.fetch_and_save_subtitle(info['abspath'], info['shash'], sub_savedir)
        
    def fetch_if_no_local_subtitles(self, sub_savedir=None):
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
            info['subtitle']['remote'] = subtitle.fetch_and_save_subtitle(info['abspath'], info['shash'], sub_savedir)
            for s in info['subtitle']['remote']:
                singleton.mplayer.send('sub_load "{0}"'.format(s))
            singleton.mplayer.send('sub_file 0')
        
    def prepare_mplayer_args(self):
        # collect media info by midentify
        self.__raw_info['mplayer'] = defaultdict(list)
        raw = self.__raw_info['mplayer']

        for l in singleton.mplayer.identify(self.args).splitlines():
            k,_,v = l.partition('=')
            raw[k].append(v)
            
        info = self.__info
        if raw['ID_VIDEO_ID']:
            from dim import apply_geometry_fix
            info['video'] = True

            # preparation
            w = int(raw['ID_VIDEO_WIDTH'][0])
            h = int(raw['ID_VIDEO_HEIGHT'][0])
            DAR_advice = float(raw['ID_VIDEO_ASPECT'][0]) if raw['ID_VIDEO_ASPECT'] else 0.0
            DAR_force = config.CMDLINE_ASPECT

            # record info
            info['width'], info['height'] = w, h
            info['DAR'], info['PAR'], args = apply_geometry_fix(w,h,DAR_advice,DAR_force)
            for item in args:
                self.add_arg(item)
                
            # subtitles
            self.parse_local_subtitles()

            # append arguments for video
            self.args += config.VIDEO_EXTRA_ARGS

        # append global arguments from command line
        self.args += config.CMDLINE_ARGS

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
            log_debug('Converting the external subtitles to UTF-8...')
            from charset import guess_locale_and_convert
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
        if not config.DEBUG:
            return
        info = self.__info
        log_items = ['Media.__del__() ---> Media info:',
                     '  Fullpath:  {}'.format(info['abspath']),
                     '  Hash:      {}'.format(info['shash'])]
        if info['video']:
            log_items.append('  Dimension: {}x{} [PAR {} DAR {}]'
                             .format(info['width'], info['height'],
                                     '{0.numerator}:{0.denominator}'.format(info['PAR']),
                                     '{0.numerator}:{0.denominator}'.format(info['DAR'])))
        log_debug('\n'.join(log_items))

