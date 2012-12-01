#!/usr/bin/env python2
# -*- coding: utf-8 -*-
#
# Copyright 2010-2012 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2012-12-02 01:21:57 by subi>
#
# mplayer-wrapper is an MPlayer frontend, trying to be a transparent interface.
# It is convenient to rename the script to "mplayer" and place it in your $PATH
# (don't overwrite the real MPlayer); you would not even notice its existence.

# TODO:
# * data persistance for
#    i)   resume last played position
#    ii)  remember last settings (volume/hue/contrast etc.)
#    iii) dedicated dir for subtitles
#    iv)  MPlayerContext
#    v)   sub_delay info from shooter
# * remember last volume/hue/contrast for continuous playing (don't need data
#   persistance)
# * shooter sometimes return a false subtitle with the same time length. find a
#   cure. (using zenity, pygtk, or both?)
# * xset s off
# * "not compiled in option"
# * detect the language in embedded subtitles, which is guaranteed to be utf8
# * use ffprobe for better(?) metainfo detection?

import logging
import os,sys
import subprocess, threading, time
import hashlib
import urllib2, struct
from fractions import Fraction
from collections import defaultdict

### Helper classes and functions
def which(prog):
    paths = [''] if os.path.isabs(prog) else os.environ['PATH'].split(os.pathsep)
    for path in paths:
        fullpath = os.path.join(path, prog)
        if os.access(fullpath, os.X_OK):
            return fullpath
    return None

def check_screen_dim():
    '''Select the maximal available screen dimension.
    '''
    dim = Dimension()
    dim_updated = False
    
    if which('xrandr'):
        for l in subprocess.check_output(['xrandr']).splitlines():
            if l.startswith('*'): # xrandr 1.1
                dim_updated = True
                
                _,w,_,h = l.split()
                if w > dim.width:
                    dim = Dimension(w,h)
            elif '*' in l:        # xrandr 1.2 and above
                dim_updated = True
                w,h = l.split()[0].split('x')
                if w > dim.width:
                    dim = Dimension(w,h)

    if not dim_updated:
        logging.info('Cannot find xrandr or unsupported xrandr version. The screen dimension defaults to {0}x{1}.'.format(dim.width, dim.height))
        
    return dim

class Dimension(object):
    def __init__(self, width = 640, height = 480):
        self.width = int(width)
        self.height = int(height)
        self.aspect = Fraction(self.width,self.height) if not self.height == 0 else Fraction(0)

def guess_enc(s, precise=False):
    ascii = '[\x09\x0A\x0D\x20-\x7E]'
    gbk = ['[\xA1-\xA9][\xA1-\xFE]',              # Level GBK/1
           '[\xB0-\xF7][\xA1-\xFE]',              # Level GBK/2
           '[\x81-\xA0][\x40-\x7E\x80-\xFE]',     # Level GBK/3
           '[\xAA-\xFE][\x40-\x7E\x80-\xA0]',     # Level GBK/4
           '[\xA8-\xA9][\x40-\x7E\x80-\xA0]']     # Level GBK/5
    big5 = ['[\xA1-\xA2][\x40-\x7E\xA1-\xFE]',
            '\xA3[\x40-\x7E\xA1-\xBF]',           # Special symbols
            '\xA3[\xC0-\xFE]',                    # Reserved, not for user-defined characters
            '[\xA4-\xC5][\x40-\x7E\xA1-\xFE]',
            '\xC6[\x40-\x7E]',                    # Frequently used characters
            '\xC6[\xA1-\xFE]',
            '[\xC7\xC8][\x40-\x7E\xA1-\xFE]',     # Reserved for user-defined characters
            '[\xC9-\xF8][\x40-\x7E\xA1-\xFE]',
            '\xF9[\x40-\x7E\xA1-\xD5]',           # Less frequently used characters
            '\xF9[\xD6-\xFE]',
            '[\xFA-\xFE][\x40-\x7E\xA1-\xFE]']    # Reserved for user-defined characters
    utf8 = ['[\xC2-\xDF][\x80-\xBF]',             # non-overlong 2-byte
            '\xE0[\xA0-\xBF][\x80-\xBF]',         # excluding overlongs
            '[\xE1-\xEC\xEE\xEF][\x80-\xBF]{2}',  # straight 3-byte
            '\xED[\x80-\x9F][\x80-\xBF]',         # excluding surrogates
            '\xF0[\x90-\xBF][\x80-\xBF]{2}',      # planes 1-3
            '[\xF1-\xF3][\x80-\xBF]{3}',          # planes 4-15
            '\xF4[\x80-\x8F][\x80-\xBF]{2}']      # plane 16

    import re
    def count_in_codec(pattern):
        '''This in necessary because ASCII can be standalone or be the low
        byte of pattern.
        '''
        if isinstance(pattern,list):
            pattern = '|'.join(pattern)
            
        # collecting all patterns
        pat = '({0}|{1})'.format(ascii, pattern)
        interpretable = ''.join(re.findall(pat, s))

        # collecting standalone ASCII by filtering 'pattern' out
        pat = '(?:{0})+'.format(pattern)
        standalone_ascii = ''.join(re.split(pat, interpretable))

        return len(standalone_ascii), len(interpretable)-len(standalone_ascii), len(s)-len(interpretable)

    # filter out ASCII as much as possible
    s = ''.join(re.split('(?:(?<![\x80-\xFE]){0})+'.format(ascii),s))
    if len(s)>2048:
        s = s[0:2048]
    threshold = int(len(s) * .005)

    # To guess the encoding of a byte string, we count the bytes those cannot
    # be interpreted by the codec.
    # http://www.w3.org/International/questions/qa-forms-utf-8
    if count_in_codec(utf8)[2] < threshold:
        return 'utf8'
    elif precise:
        # GB2312 and BIG5 share lots of code points and hence sometimes we need a more precise method.
        # http://www.ibiblio.org/pub/packages/ccic/software/data/chrecog.gb.html
        l = len(re.findall('[\xA1-\xFE][\x40-\x7E]',s))
        h = len(re.findall('[\xA1-\xFE][\xA1-\xFE]',s))
        if l == 0:
            return 'gb2312'
        elif float(l)/float(h) < 0.25:
            return 'gbk'
        else:
            return 'big5'
    elif count_in_codec(gbk)[2] < threshold:
        # Favor GBK over BIG5. If this not you want, just set precise=True
        return 'gbk'
    elif count_in_codec(big5)[2] < threshold:
        return 'big5'
    else:
        return 'unknown'
            
def utf8lize(s):
    enc = guess_enc(s)
    if enc in ['utf8','unknown']:
        return s
    else:
        return s.decode(enc,'ignore').encode('utf8')

### Application classes
class Application(object):
    def __init__(self, args):
        if '--debug' in args:
            logging.root.setLevel(logging.DEBUG)
            args.remove('--debug')
        if '--dry-run' in args:
            logging.root.setLevel(logging.DEBUG)
            args.remove('--dry-run')
            config['dry-run'] = True

class Identifier(Application):
    def run(self):
        print MPlayer().identify(args)
    def __init__(self,args):
        super(Identifier, self).__init__(args)
        self.args = args

class MPlayerFifo(object):
    def send(self, s):
        if self.args:
            logging.debug('Sending command "{0}" to {1}...'.format(s, self.__path))
            with open(self.__path,'w') as f:
                f.write(s+'\n')
        else:
            logging.info('"{0}" cannot be sent to the unexist {1}.'.format(s, self.__path))
    
    def __init__(self):
        self.args = []
        
        xdg = os.environ['XDG_RUNTIME_DIR']
        if xdg:
            self.__tmpdir = None
            self.__path = os.path.join(xdg, 'mplayer.fifo')
        else:
            import tempfile
            self.__tmpdir = tempfile.mkdtemp()
            self.__path = os.path.join(self.__tmpdir, 'mplayer.fifo')

        try:
            os.mkfifo(self.__path)
#            import atexit
#            atexit.register(lambda f: os.unlink(f), self.__path)

            self.args = '-input file={0}'.format(self.__path).split()
        except OSError, e:
            logging.info(e)
            
    def __del__(self):
        if self.args:
            os.unlink(self.__path)
        if self.__tmpdir:
            os.rmdir(self.__tmpdir)
            
class Player(Application):
    def __init__(self, args):
        super(Player, self).__init__(args)

        self.mplayer = MPlayer()
        self.fifo = MPlayerFifo() if not self.dry_run else None

        while args:
            s = args.pop(0)
            if s == '--':
                self.files.extend(args)
                args = []
            elif s.startswith('-'):
                flag = self.mplayer.has_opt(s.partition('-')[2])
                if flag == 0:
                    self.bad_args.append(s)
                elif flag == 1:
                    self.args.append(s)
                elif flag == 2:
                    self.args.append(s)
                    if args:
                        self.args.append(args.pop(0))
            else:
                self.files.append(s)
            
        if self.bad_args:
            logging.info('Unsupported options "' + ' '.join(self.bad_args) + '" are automatically suppressed.')

    def send(self, cmd):
        if self.mplayer.has_active_instance():
            self.fifo.send(cmd)
        else:
            logging.debug('Command "{0}" discarded.'.format(cmd))
            
    def run(self):
        files = generate_filelist(self.files)

        if not files:
            self.mplayer.run(self.args)
        else:
            for f in files:
                args = []
                m = self.mplayer.probe(f)
                if m['video']:
                    args += '-subcp utf8'.split()
                    if self.fifo:
                        args += self.fifo.args

                    use_ass = False if '-noass' in self.args or not self.mplayer.support_ass() else True
                    args += expand_video(m, use_ass, self.mplayer.is_mplayer2())

                    # now handle subtitles
                    if not self.dry_run:
                        need_fetch = True
                        for subs in m['subtitles']:
                            if subs[0] == 'External Text':
                                logging.debug('Convert the subtitles to UTF-8.')
                                for sub in (subs[1] if isinstance(subs[1],list) else [subs[1]]):
                                    with open(sub,'rb') as ff:
                                        s = utf8lize(ff.read())
                                    with open(sub,'wb') as ff:
                                        ff.write(s)
                                need_fetch = False
                            elif subs[0] == 'Embedded Text':
                                need_fetch = False
                            else:
                                pass
                        if need_fetch:
                            # so we have to weakref self to break the implicit
                            # circular references.
                            import weakref
#                            fetch_thread = threading.Thread(target=SubFetcher().fetch, args=(m['fullpath'],m['hash'],weakref.ref(self)))
                            fetch_thread = threading.Thread(target=SubFetcher().fetch, args=(m['fullpath'],m['hash'],self))
                            fetch_thread.daemon = True
                            fetch_thread.start()

                args += self.args
                args += [f]

                self.mplayer.run(args, self.dry_run)
                if self.mplayer.last_exit_status == 'Quit':
                    break

class Fetcher(Application):
    def send(self, cmd):
        logging.info(cmd)
    def run(self):
        if not self.files:
            print '请指定需要下载字幕的视频文件'
            
        for f in self.files:
            m = Media(f)
            if not m['abspath']: # file not exist
                continue

            self.fetcher.fetch(m['abspath'],m['shash'],self,self.savedir,config['dry_run'])

    def __init__(self, args):
        self.fetcher = SubFetcher()
        self.savedir = None
        self.files = []
        
        super(Fetcher,self).__init__(args)
        for arg in args:
            if arg.startswith('--savedir'):
                self.savedir = arg.split('=')[1]
            else:
                self.files += [arg]

### Main modules
class Media(defaultdict):
    def __init__(self, path):
        super(Media, self).__init__(bool)

        if not os.path.exists(path):
            return

        self['path'] = path
        self['abspath'] = os.path.abspath(path)

        sz = os.path.getsize(path)
        if sz>8192:
            with open(path, 'rb') as f:
                self['shash'] = ';'.join([(f.seek(s), hashlib.md5(f.read(4096)).hexdigest())[1] for s in (lambda l:[4096, l/3*2, l/3, l-8192])(sz)])

class MPlayer(object):
    last_timestamp = 0.0
    last_exit_status = None

    def identify(self, args):
        args = [self.exe_path] + '-vo null -ao null -frames 0 -identify'.split() + args
        if config['dry-run']:
            return 'Will execute:\n  {0}'.format(' '.join(args))
        else:
            return '\n'.join([l for l in subprocess.check_output(args).splitlines() if l.startswith('ID_')])

    def probe(self, filename):
        info = {}
        for l in self.identify([filename]):
            a = l.partition('=')
            if a[0] in info:
                info[a[0]] = [info[a[0]]] + [a[2]]
            else:
                info[a[0]] = a[2]

        from collections import defaultdict
        ret = defaultdict(bool)
        ret['filename'] = filename
        ret['fullpath'] = os.path.abspath(filename)
        if 'ID_FILENAME' in info:
            ret['seekable'] = (info['ID_SEEKABLE'] == '1')
            ret['video'] = True if 'ID_VIDEO_ID' in info else False
        
            if ret['video']:
                # Aspect Ratios and Frame Sizes
                # reference: http://www.mir.com/DMG/aspect.html
                ret['frame'] = Dimension(info['ID_VIDEO_WIDTH'], info['ID_VIDEO_HEIGHT'])
                ret['DAR'] = ret['frame'].aspect
                ret['SAR'] = 1
                if 'ID_VIDEO_ASPECT' in info and float(info['ID_VIDEO_ASPECT']) != 0:
                    # Display Aspect Ratio: 4:3 or 16:9
                    ret['DAR'] = Fraction(info['ID_VIDEO_ASPECT']).limit_denominator(10)
                    # Sample/Pixel Aspect Ratio: 
                    ret['SAR'] = (ret['DAR'] / ret['frame'].aspect).limit_denominator(82)

                # Unique (hopefully) hash for shooter subtitle search
                sz = os.path.getsize(ret['fullpath'])
                if sz>8192:
                    with open(ret['fullpath'], 'rb') as f:
                        ret['hash'] = ';'.join([(f.seek(s), hashlib.md5(f.read(4096)).hexdigest())[1] for s in (lambda l:[4096, l/3*2, l/3, l-8192])(sz)])
                else:
                    ret['hash'] = ';;;'

                ret['subtitles'] = []
                if 'ID_SUBTITLE_ID' in info:
                    ret['subtitles'] += [('Embedded Text', '<Embedded Text>')]
                if 'ID_FILE_SUB_ID' in info:
                    ret['subtitles'] += [('External Text', info['ID_FILE_SUB_FILENAME'])]
                if 'ID_VOBSUB_ID' in info:
                    ret['subtitles'] += [('External Vobsub', info['ID_VOBSUB_FILENAME'])]

        items = ['\n'
                 '  Fullpath:         {0}\n'
                 '  Seekable:         {1}\n'
                 '  Video:            {2}\n'.format(ret['fullpath'], ret['seekable'], ret['video'])]
        if ret['video']:
            items.append('    Dimension:      {0} [SAR {1} DAR {2}]\n'
                         '    Hash:           {3}\n'
                         .format('{0.width}x{0.height}'.format(ret['frame']),
                                 '{0.numerator}:{0.denominator}'.format(ret['SAR']),
                                 '{0.numerator}:{0.denominator}'.format(ret['DAR']),
                                 ret['hash']))

            items.append('    Subtitles:\n')
            for sub in ret['subtitles']:
                items.append('      {0}:'.format(sub[0]))
                subb = sub[1] if isinstance(sub[1], list) else [sub[1]]
                items.append('\n                    '.join(subb))
                
        logging.debug(''.join(items))
        return ret
        
    def run(self, args, dry_run=False):
        args = [self.exe_path] + args
        logging.debug('\n'+' '.join(args))
        if not dry_run:
            self.__process = subprocess.Popen(args, stdin=sys.stdin, stdout=subprocess.PIPE, stderr=None)
            self.__tee()

    def has_active_instance(self):
        return self.__process != None
    
    def support_ass(self):
        if self.__ass == None:
            self.__ass = True
            if not self.has_opt('ass'):
                self.__ass = False
            else:
                libass_path = None
                for l in subprocess.check_output(['ldd',self.exe_path]).splitlines():
                    if 'libass' in l:
                        libass_path = l.split()[2]
                if not libass_path:
                    self.__ass = False
                else:
                    if not 'libfontconfig' in subprocess.check_output(['ldd',libass_path]):
                        self.__ass = False
        return self.__ass
    
    def is_mplayer2(self):
        if self.__mplayer2 == None:
            if 'MPlayer2' in subprocess.check_output([self.exe_path]).splitlines()[0]:
                logging.debug('Is a MPlayer2 fork.')
                self.__mplayer2 = True
            else:
                self.__mplayer2 = False
        return self.__mplayer2
    
    def has_opt(self, opt):
        '''return value:
        0: don't have the option
        1: have it and take no param
        2: have it and take 1 param
        '''
        if not self.__opts:
            self.__gen_opts()
        return self.__opts[opt] if opt in self.__opts else 0

    def __init__(self):
        self.exe_path = None
        for p in ['/opt/bin/mplayer','/usr/local/bin/mplayer','/usr/bin/mplayer']:
            if which(p):
                self.exe_path = p
                break
        if not self.exe_path:
            raise RuntimeError,'Cannot find a mplayer binary.'

        self.__mplayer2 = None
        self.__ass = None
        self.__opts = {}

    def __gen_opts(self):
        options = subprocess.Popen([self.exe_path, '-list-options'], stdout=subprocess.PIPE).communicate()[0].splitlines()
        if self.is_mplayer2():
            options = options[3:len(options)-3]
        else:
            options = options[3:len(options)-4]

        for line in options:
            s = line.split()
            opt = s[0].split(':') # take care of option:suboption
            if opt[0] in self.__opts:
                continue
            self.__opts[opt[0]] = (2 if len(opt)==2 or s[1]!='Flag' else 1)

        # handle vf* af*:
        # mplayer reports option name as vf*, which is a family of options.
        del self.__opts['af*']
        del self.__opts['vf*']
        for extra in ['af','af-adv','af-add','af-pre','af-del','vf','vf-add','vf-pre','vf-del']:
            self.__opts[extra] = 2
        for extra in ['af-clr','vf-clr']:
            self.__opts[extra] = 1

    def __tee(self):
        def flush_first_line(fileobj, lines):
            fileobj.write(''.join(lines.pop(0)))
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
            if c == '\n':
                flush_first_line(f,lines)
            elif c == '\r':
                d = p.stdout.read(1)
                if d == '\n':
                    lines[4].append('\n')
                    flush_first_line(f,lines)
                else:
                    flush_first_line(f,lines)
                    lines[4].append(d)
            else:
                pass

        # save info and flush rest outputs
        for l in (''.join(ll) for ll in lines):
            if l.startswith(('A:','V:')):
                try:
                    self.last_timestamp = float(l[2:9])
                except ValueError:
                    pass
            if l.startswith('Exiting...'):
                self.last_exit_status = l[12:len(l)-2]
            f.write(l)
        f.flush()

        logging.debug('Last timestamp: {0}'.format(self.last_timestamp))
        logging.debug('Last exit status: {0}'.format(self.last_exit_status))
        self.__process = None

def expand_video(media, use_ass=True, mplayer2=False):
    '''Video-expanding attaches two black bands to the top and bottom of the
    video. MPlayer can then render OSDs/texts (time display, subtitles, etc.)
    within the bands.
    
    Two ways exist:
    1. -vf expand:
       everything done by mplayer, not compatible with libass (subtitle-
       overlap). Have to use the plain subtitle renderer (-noass) instead.
    2. -ass-use-margin:
       everything done by YOU, including the calculation of the margin heights
       and the font scales. The benefit is you can use "-ass".
        
    The "ass-use-margin" method has an annoying bug: the subtitle characters
    are of the wrong aspect. (Fixed in mplayer but not mplayer2.)
    
    After some googling and experiments, here comes the magic:
    1. there are 2 different frames: the video (in X, Y) and the ass rendering
       frame (in PlayResX, PlayResY)
       a. PlayResY defaults to 288
       b. PlayResX defaults to PlayResY/1.3333, which is 384
       c. Since we never change PlayResX, PlayResY, we use 384, 288
          directly
    2. the font scale is calculated w.r.t. the ASS styles of "PlayResX,
       PlayResY, ScaleX, ScaleY" and the mplayer option "-ass-font-scale":
       a. ScaleX, ScaleY default to 1
       b. the font size rendered in the VIDEO frame is:
               aspect_scale = X:Y / 384:288
               vertical_scale = Y/288
               fontX = ScaleX * vertical_scale / aspect_scale
               fontY = ScaleY * vertical_scale / aspect_scale
    3. the ass-font-scale will then be multiplied to all the calculated font
       size

    The most concerned is the font size rendered in the physical screen (in X',
    Y'):
               vertical_scale' = Y'/288
               fontX' = ScaleX * vertical_scale' / aspect_scale
               fontY' = ScaleY * vertical_scale' / aspect_scale
    To make the subtitle be of the same size in the same screen (with different
    videos), let ass-font-scale *= aspect_scale.
    
    While the video expanding (in Y axis) is concerned, the situation becomes a
    little messy:
             aspect_scale" = X:Y" / 384:288
             vertical_scale" = Y"/288
             fontX" = ScaleX * vertical_scale" / aspect_scale      (*)
                    = fontX * (Y"/Y)
             fontY" = ScaleY * vertical_scale" / aspect_scale"
                    = fontY
    Clearly the subtitle is horizontally stretched (note (*), which is fixed in
    mplayer). Of course you should let ass-font-scale *= aspect_scale" if fixed
    font size in physical screen is required.
        
    So, what we need are:
    1. do expanding
    2. fix stretched font (simply let ScaleX = Y/Y" )
    3. choose an appropriate scale of font
        
    We also want to place the subtitle as close to the picture as possible
    (instead of the very bottom of the screen, which is visual distracting).
    This can be done via the ASS style tag "MarginV", which denotes the
    relative vertical margin in the ass rendering screen (i.e. the screen of
    PlayResX:PlayResY).
        
    Another approach is just adding a black band with adequate width to contain
    the OSDs/texts, avoiding ugly "MarginV".
    '''
    # mplayer option "-subfont-autoscale" affects all osds/texts because we
    # will change the movie height, only mode 2 is applicable, in which is
    # proportional to movie width
    scaling_mode = 2

    # text scales: make the fonts fixed size in fullscreen (independent on the
    # video size)
    osd_scale = 3
    text_scale = 4.5

    # ass font scale:
    if use_ass:
        ass_scale = 1.5
        # recall that ass font is rendered in a 384x288 canvas 1.8 lines of
        # 18-pixels font in screen of height 288
        ass_margin_scale = ass_scale * 18/288 * 1.8

    screen_aspect = check_screen_dim().aspect

    # basic options
    args = '-subfont-autoscale {0} -subfont-osd-scale {1}'.format(scaling_mode, osd_scale).split()

    # do expansion
    if media['DAR'] < Fraction(4,3):
        # if the video is too narrow (<4:3), force 4:3
        args.extend('-vf-pre dsize=4/3'.split())
    elif use_ass:
        aspect_scale = media['DAR'] / Fraction(4,3)
        # Y"/Y = X:Y / X:Y"
        # i.e. vertical_ratio = DAR / expanded_DAR
        vertical_ratio = min(1.0 + 2 * ass_margin_scale, media['DAR'] / screen_aspect)
        ass_margin_scale = (vertical_ratio - 1.0) / 2

        if ass_margin_scale > 0:
            aspect_scale /= vertical_ratio
            margin = int(ass_margin_scale * media['frame'].height)
            args.extend('-ass-use-margins -ass-bottom-margin {0} -ass-top-margin {0}'.format(margin).split())
            if mplayer2:
                args.extend('-ass-force-style ScaleX={0}'.format(1.0/vertical_ratio).split())
                
        args.extend('-ass -ass-font-scale {0}'.format(ass_scale * aspect_scale).split());
    else:
        args.extend('-subpos 98 -subfont-text-scale {0} -vf-pre expand={1}::::1:{2}'
                    .format(text_scale, media['frame'].width, screen_aspect).split())

    return args

def parse_shooter_package(fileobj):
    '''Parse shooter returned package of subtitles.
    Return subtitles encoded in UTF-8.
    '''
    subtitles = []
    f = fileobj

    # read contents
    c = f.read(1)
    package_count = struct.unpack('!b', c)[0]

    for i in range(package_count):
        # note: var '_' is the length of the following bytestream, but is
        # useless.
        c = f.read(8)
        _,desc_length = struct.unpack('!II', c)
        description = f.read(desc_length).decode('utf8')
        if not description:
            description = ''
        else:
            if 'delay' in description:
                sub_delay = description.partition('=')[2] / 1000.0
            else:
                sub_delay = 0
            description = ' ({0})'.format(description)

        c = f.read(5)
        _,file_count = struct.unpack('!IB', c)
            
        for j in range(file_count):
            c = f.read(8)
            _,ext_len = struct.unpack('!II', c)
            ext = f.read(ext_len)

            c = f.read(4)
            file_len = struct.unpack('!I', c)[0]
            sub = f.read(file_len)
            if sub.startswith('\x1f\x8b'):
                import gzip
                from cStringIO import StringIO
                sub = gzip.GzipFile(fileobj=StringIO(sub)).read()

            subtitles.append({'extension': ext,
                              'delay': sub_delay,
                              'content': sub})

    logging.debug('{0} subtitle(s) fetched.'.format(len(subtitles)))
    logging.debug('Filtering duplicated subtitles...')
    dup_tag = [False]*len(subtitles)
    for i in range(len(subtitles)):
        if dup_tag[i]:
            continue
        for j in range(i+1, len(subtitles)):
            if subtitles[i]['extension'] != subtitles[j]['extension']:
                continue
            sa = subtitles[i]['content']
            sb = subtitles[j]['content']
            import difflib
            similarity = difflib.SequenceMatcher(None, sa, sb).real_quick_ratio()
            logging.debug('Similarity is {0}.'.format(similarity))
            if similarity > 0.7:
                dup_tag[j] = True
    subtitles = [subtitles[i] for i in range(len(subtitles)) if not dup_tag[i]]

    logging.debug('Convert the current subtitle to UTF-8.')
    for sub in subtitles:
        sub['content'] = utf8lize(sub['content'])
    
    logging.debug('{0} subtitle(s) parsed.'.format(len(subtitles)))
    return subtitles

class SubtitleHandler(object):
    def fetch():
        pass
    def save():
        pass
    def load():
        pass
    def encode():
        pass

class SubFetcher(object):
    subtitles = []
    
    def fetch(self, filepath, filehash, app, save_dir=None, dry_run=False):
        # wait for mplayer to settle up
        time.sleep(3)

        app.send('osd_show_text "正在查询字幕..." 5000')

        # fetch
        for i, t in enumerate(self.__tries):
            try:
                logging.debug('Wait for {0}s to connect to shooter server ({1})...'.format(t,i))
                time.sleep(t)

                self.__build_req(filepath, filehash)

                if dry_run:
                    break

                response = urllib2.urlopen(self.__req)
                self.subtitles = parse_shooter_package(response)
                response.close()
                
                if self.subtitles:
                    break
            except urllib2.URLError, e:
                logging.debug(e)

        # save
        if self.subtitles:
            if save_dir:
                prefix = os.path.join(save_dir,os.path.splitext(os.path.basename(filepath))[0])
            else:
                prefix = os.path.splitext(filepath)[0]

            # save subtitles and generate mplayer fifo commands
            for i,s in enumerate(self.subtitles):
                suffix = (str(i) if i>0 else '')
                path = prefix + suffix + '.' + s['extension']
                if os.path.exists(path):
                    path = prefix + suffix + '-1.' + s['extension']
                logging.info('Saving the subtitle as {0}'.format(path))
                with open(path,'wb') as f:
                    f.write(s['content'])
                app.send('sub_load "{0}"'.format(path))
                app.send('sub_delay "{0}"'.format(s['delay']))
            app.send('sub_file 0')
        else:
            logging.info('Failed to fetch subtitles.')
            app.send('osd_show_text "查询字幕失败." 3000')
    
    def __init__(self):
        import httplib
        self.__schemas = ['http', 'https'] if hasattr(httplib, 'HTTPS') else ['http']
        self.__servers = ['www', 'splayer', 'svplayer'] + ['splayer'+str(i) for i in range(1,13)]

        self.__req = None
        self.__tries = [0, 10, 30, 60, 120]
        self.__fetch_successful = False

    def __build_req(self, filepath, filehash):
        self.__rev = 2437                               # as of 2012-07-02
        self.__filehash = filehash
        self.__pathinfo= '\\'.join(['D:',
                                    os.path.basename(os.path.dirname(filepath)),
                                    os.path.basename(filepath)])
        vhash_base = 'SP,aerSP,aer {0} &e(\xd7\x02 {1} {2}'.format(self.__rev,
                                                                   self.__pathinfo,
                                                                   self.__filehash)
        self.__vhash = hashlib.md5(vhash_base).hexdigest()

        import random
        boundary = '-'*28 + '{0:x}'.format(random.getrandbits(48))

        url = '{0}://{1}.shooter.cn/api/subapi.php'.format(random.choice(self.__schemas),
                                                           random.choice(self.__servers))

        header = []
        header.append(['User-Agent', 'SPlayer Build {0}'.format(self.__rev)])
        header.append(['Content-Type', 'multipart/form-data; boundary={0}'.format(boundary)])

        items = []
        items.append(['filehash', self.__filehash])
        items.append(['pathinfo', self.__pathinfo])
        items.append(['vhash', self.__vhash])
        
        data = ''.join(['--{0}\n'
                        'Content-Disposition: form-data; name="{1}"\n\n'
                        '{2}\n'.format(boundary, d[0], d[1]) for d in items]
                       + ['--' + boundary + '--'])

        logging.debug('Querying server {0} with\n'
                      '{1}\n'
                      '{2}\n'.format(url,header,data))

        self.__req = urllib2.Request(url)
        for h in header:
            self.__req.add_header(h[0],h[1])
        self.__req.add_data(data)

def generate_filelist(files):
    '''Generate a list for continuous playing.
    '''
    if not len(files)==1 or not os.path.exists(files[0]):
        return files
        
    def translate(s):
        import locale
        dic = dict(zip(u'零壹贰叁肆伍陆柒捌玖〇一二三四五六七八九','01234567890123456789'))
        loc = locale.getdefaultlocale()
        s = s.decode(loc[1])
        return ''.join([dic.get(c,c) for c in s]).encode(loc[1])
    def split_by_int(s):
        import re
        return [x for x in re.split('(\d+)', translate(s)) if x != '']
    def make_sort_key(s):
        return [(int(x) if x.isdigit() else x) for x in split_by_int(s)]
    def strip_to_int(s,prefix):
        if prefix and prefix in s:
            s = s.partition(prefix)[2]
        s = split_by_int(s)[0]
        return int(s) if s.isdigit() else float('NaN')
    
    pdir, basename = os.path.split(os.path.abspath(files[0]))

    # basic candidate filtering
    # 1. extention
    files = [f for f in os.listdir(pdir) if f.endswith(os.path.splitext(basename)[1])]
    # 2. remove previous episodes
    files.sort(key=make_sort_key)
    del files[0:files.index(basename)]

    # not necessary to go further if only one candidate
    if len(files) == 1:
        return [os.path.join(pdir,basename)]

    # find the common prefix
    keys = [split_by_int(f) for f in files[0:2]]
    prefix_items = []
    for key in zip(keys[0],keys[1]):
        if key[0] == key[1]:
            prefix_items.append(key[0])
        else:
            break
    prefix = ''.join(prefix_items)

    # generate the list
    results = [os.path.join(pdir,files[0])]
    for i,f in enumerate(files[1:]):
        if strip_to_int(f,prefix) - strip_to_int(files[i],prefix) == 1:
            results.append(os.path.join(pdir,f))
        else:
            break
    return results

if __name__ == '__main__':
    if sys.hexversion < 0x02070000:
        print 'Please run the script with python>=2.7'
    else: 
        config = defaultdict(bool)

        logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)
        args = sys.argv[:]
        name = os.path.basename(args.pop(0))
        if 'mplayer' in name:
            app = Player
        elif 'mfetch' in name:
            app = Fetcher
        elif 'midentify' in name:
            app = Identifier
        else:
            app = Application
#        print Media(sys.argv[1])
        app(args).run()
