#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-07-31 17:25:21 by subi>

from globals import config, singleton

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
            
    def __del__(self):
        singleton.clean()
        
class Identifier(Application):
    def __init__(self,args):
        super(Identifier, self).__init__(args)
        self.args = args

    def run(self):
        from mplayer import MPlayer
        print(MPlayer(minimal=True).identify(self.args))
        
class Fetcher(Application):
    def __init__(self, args):
        super(Fetcher,self).__init__(args)
        self.savedir = None
        self.files = []
        for arg in args:
            if arg.startswith('--savedir'):
                self.savedir = arg.split('=')[1]
            else:
                self.files.append(arg)

    def run(self):
        from media import Media
        for f in self.files:
            Media(f).fetch_remote_subtitles(self.savedir)
            
class Player(Application):
    def __init__(self, args):
        # the parent handles --dry-run, --debug.
        super(Player, self).__init__(args)

        # the mplayer instance handles all its recognizable arguments.
        singleton.create_mplayer(args)
        
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

        # notify about the invalid arguments.
        if invalid:
            from globals import log_info
            log_info('Unknown option(s) "' + ' '.join(invalid) + '" are ignored.')

    def run(self):
        if not self.playlist:
            singleton.get_mplayer().play()
        else:
            self.__run_playlist()

    def __run_playlist(self):
        import threading, time
        # Use a separate thread to reduce the noticeable lag when finding
        # episodes in a big directory.
        def generate_playlist(playlist_seed, lock):
            from aux import find_more_episodes
            time.sleep(1.5)
            with lock:
                self.playlist += find_more_episodes(playlist_seed)
        playlist_lock = threading.Lock()
        playlist_thread = threading.Thread(target=generate_playlist, args=(self.playlist[-1],playlist_lock))
        playlist_thread.daemon = True
        playlist_thread.start()

        # Watchdog thread
        def watch(m):
            # wait for media setting up
            time.sleep(3.0)
            m.fetch_if_no_local_subtitles()
        from media import Media
        while self.playlist:
            with playlist_lock:
                f = self.playlist.pop(0)
            m = Media(f)
            watch_thread = threading.Thread(target=watch, args=(m,))
            watch_thread.daemon = True
            watch_thread.start()

            m.play()
            
            if singleton.get_mplayer().last_exit_status == 'Quit':
                break

            playlist_thread.join()
            
            
