# global imports
from __future__ import unicode_literals
import sys,os

from aux import log

# config
class config(object):
    DEBUG=False
    DRY_RUN=False

    CMDLINE_ASPECT=None
    CMDLINE_ARGS=[]
    VIDEO_EXTRA_ARGS=[]
    
    CACHE_DIR=None

    @staticmethod
    def get_cache_dir():
        if not config.CACHE_DIR:
            cache_home = os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache'))
            config.CACHE_DIR = os.path.join(cache_home, 'mplayer-wrapper')
        return config.CACHE_DIR

# singleton
class singleton(object):
    __mplayer = None
    __notifier = None

    @staticmethod
    def clean():
        if singleton.__mplayer:
            del singleton.__mplayer
            
    @staticmethod
    def create_mplayer(args=[]):
        if singleton.__mplayer != None:
            log_info('There is already an MPlayer instance. Replacing by default.')
            del singleton.__mplayer

        import mplayer
        singleton.__mplayer = mplayer.MPlayer(args)
        return singleton.__mplayer
            
    @staticmethod
    def get_mplayer():
        if singleton.__mplayer == None:
            log_debug('There is no MPlayer instance. Creating by default.')
            import mplayer
            singleton.__mplayer = mplayer.MPlayer()
        return singleton.__mplayer

# logging function according to debug level
def log_info(s):
    log(s)
def log_debug(s):
    if config.DEBUG:
        log(s)
