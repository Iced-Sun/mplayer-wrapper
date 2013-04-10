import os

class config(object):
    DEBUG=False
    DRY_RUN=False

    CMDLINE_ASPECT=None
    
    CACHE_DIR=None

    @staticmethod
    def get_cache_dir():
        if not config.CACHE_DIR:
            cache_home = os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache'))
            config.CACHE_DIR = os.path.join(cache_home, 'mplayer-wrapper')
        return config.CACHE_DIR

    
class singleton(object):
    mplayer = None
