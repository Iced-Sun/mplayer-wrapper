#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-04-10 12:48:36 by subi>
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
import os,sys

from global_setting import *
from aux import fsdecode

### Application classes
class Application(object):
    '''The application class should:
    1. parse command line arguments
    2. provide run() method to accomplish its role
    '''
    def __init__(self, args):
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
        from mplayer import MPlayerContext
        print(MPlayerContext().identify(self.args))
        
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
        from media import Media
        for f in self.files:
            Media(f).fetch_remote_subtitles_and_save(sub_savedir=self.savedir)
            
class Player(Application):
    from mplayer import MPlayer
    from media import Media
    
    def __init__(self, args):
        super(Player, self).__init__(args)
        self.args = defaultdict(list)

        self.mplayer = MPlayer(args)

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
            import threading
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
        from aux import find_more_episodes
        import time
        time.sleep(1.5)
        with lock:
            self.playlist += find_more_episodes(self.args['file'][-1])

if __name__ == '__main__':
    if sys.hexversion < 0x02070000:
        print 'Please run the script with python>=2.7'
    else:
        args = [fsdecode(x) for x in sys.argv]
        name = os.path.basename(args.pop(0))

        if 'mplayer' in name:
            if 'fetch' == args[0]:
                args.pop(0)
                app = Fetcher
            elif 'identify' == args[0]:
                args.pop(0)
                app = Identifier
            elif 'play' == args[0]:
                args.pop(0)
                app = Player
            else:
                app = Player
        elif 'mfetch' in name:
            app = Fetcher
        elif 'midentify' in name:
            app = Identifier
        else:
            app = Application

        app(args).run()
        
