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
    def create_mplayer(args=None):
        if singleton.__mplayer != None:
            raise Exception('There is already a MPlayer instance.')
        else:
            import mplayer
            singleton.__mplayer = mplayer.MPlayer(args)
        return singleton.__mplayer
            
    @staticmethod
    def get_mplayer():
        if singleton.__mplayer == None:
            raise Exception('There is no MPlayer instance.')
        return singleton.__mplayer

# logging function according to debug level
def log_info(s):
    log(s)
def log_debug(s):
    if config.DEBUG:
        log(s)
