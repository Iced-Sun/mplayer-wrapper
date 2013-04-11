#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-04-11 18:47:27 by subi>
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
# * use defaultdict wisely

import os, sys

from aux import fsdecode
from app import *

if __name__ == '__main__':
    if sys.hexversion < 0x02070000:
        print('Please run the script with python>=2.7')
    else:
        args = [fsdecode(x) for x in sys.argv]
        name = os.path.basename(args.pop(0))

        if 'mplayer' in name:
            app = Player
            if len(args) > 0:
                if 'fetch' == args[0]:
                    args.pop(0)
                    app = Fetcher
                elif 'identify' == args[0]:
                    args.pop(0)
                    app = Identifier
                elif 'play' == args[0]:
                    args.pop(0)
        elif 'mfetch' in name:
            app = Fetcher
        elif 'midentify' in name:
            app = Identifier
        else:
            app = Application

        app(args).run()

