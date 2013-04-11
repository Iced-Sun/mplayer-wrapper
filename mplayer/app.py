#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-04-11 19:19:10 by subi>

from global_setting import config, singleton

class Application(object):
    '''The application class should:
    1. parse command line arguments
    2. provide run() method to accomplish its role
    '''
    def __init__(self, args):
        if '--debug' in args:
            args.remove('--debug')
            config.DEBUG = True
        if '--dry-run' in args:
            args.remove('--dry-run')
            config.DEBUG = True
            config.DRY_RUN = True

class Identifier(Application):
    def __init__(self,args):
        super(Identifier, self).__init__(args)
        self.args = args

    def run(self):
        from mplayer import MPlayer
        print(MPlayer(minimal=True).identify(self.args))
        
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
    def __init__(self, args):
        # the parent handles --dry-run, --debug.
        super(Player, self).__init__(args)

        # the mplayer instance handles all its recognizable arguments.
        from mplayer import MPlayer
        singleton.mplayer = MPlayer(args)

        # handle the left arguments
        self.playlist = []
        invalid = []

        while args:
            s = args.pop(0)
            if s == '--':
                self.playlist += args
                args = []
            elif s.startswith('-'):
                invalid.append(s)
            else:
                self.playlist.append(s)

        self.__playlist_seed = self.playlist[-1]
        
        if invalid:
            from global_setting import log_info
            log_info('Unknown option(s) "' + ' '.join(invalid) + '" are ignored.')

    def run(self):
        if not self.playlist:
            singleton.mplayer.play()
        else:
            self.__run_playlist()

    def __run_playlist(self):
        import threading
        # Use a separate thread to reduce the noticeable lag when finding
        # episodes in a big directory.
        playlist_lock = threading.Lock()
        playlist_thread = threading.Thread(target=self.generate_playlist, args=(playlist_lock,))
        playlist_thread.daemon = True
        playlist_thread.start()
            
        from media import Media
        while self.playlist:
            with playlist_lock:
                f = self.playlist.pop(0)
            m = Media(f)
            m.prepare_mplayer_args()
            watch_thread = threading.Thread(target=self.watch, args=(m,))
            watch_thread.daemon = True
            watch_thread.start()

            singleton.mplayer.play(m)
            if singleton.mplayer.last_exit_status == 'Quit':
                break

            playlist_thread.join()
            
    def watch(self, media):
        if media.is_video():
            media.fetch_remote_subtitles_and_save(load_in_mplayer=True)
            
    def generate_playlist(self, lock):
        import time
        time.sleep(1.5)
        from aux import find_more_episodes
        with lock:
            self.playlist += find_more_episodes(self.__playlist_seed)

