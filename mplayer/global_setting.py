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
    mplayer = None

# logging function according to debug level
def log_info(s):
    log(s)
def log_debug(s):
    if config.DEBUG:
        log(s)
