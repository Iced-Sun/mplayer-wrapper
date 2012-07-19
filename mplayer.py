#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2012 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2012-07-19 22:58:31 by subi>
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
import os, sys, time
import struct, urllib2
import locale, re
import subprocess, threading
import hashlib
from fractions import Fraction

# http://www.python.org/dev/peps/pep-0318/
def singleton(cls):
    instances = {}
    def getinstance():
        if cls not in instances:
            instances[cls] = cls()
        return instances[cls]
    return getinstance

def which(cmd):
    def exefy(fullpath):
        return fullpath if os.access(fullpath, os.X_OK) else None

    pdir = os.path.split(cmd)[0]
    if pdir:
        return exefy(cmd)
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            fullpath = exefy(os.path.join(path,cmd))
            if fullpath:
                return fullpath
    return None

def check_screen_dim():
    """Select the maximal available screen dimension.
    """
    dim = Dimension()
    if which("xrandr"):
        for l in subprocess.check_output(["xrandr"]).splitlines():
            if l.startswith("*"):
                # xrandr 1.1: select the first occurrence
                dim = Dimension(l.split()[1], l.split()[3])
                break
            elif '*' in l:
                d = l.split()[0].split('x')
                if d[0] > dim.width:
                    dim = Dimension(d[0],d[1])
                    
    return dim

class Dimension(object):
    def __init__(self, width = 640, height = 480):
        self.width = int(width)
        self.height = int(height)
        self.aspect = Fraction(self.width,self.height) if not self.height == 0 else Fraction(0)

@singleton
class Fifo(object):
    def __init__(self):
        if dry_run:
            self.args = ""
        else:
            import tempfile
            self.__tmpdir = tempfile.mkdtemp()
            self.__path = os.path.join(self.__tmpdir, "mplayer_fifo")
            self.args = "-input file={0}".format(self.__path).split()
            os.mkfifo(self.__path)

    def __del__(self):
        if self.args:
            os.unlink(self.__path)
            os.rmdir(self.__tmpdir)

class VideoExpander(object):
    """Video expanding attaches two black bands to the top and bottom of the
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
             fontX" = ScaleX * vertical_scale" / aspect_scale"
                    = fontX
             fontY" = ScaleY * vertical_scale / aspect_scale"       (*)
                    = fontY / (Y"/Y)
    Clearly the subtitle is horizontally stretched (note (*), which is fixed in
    mplayer). Of course you should let ass-font-scale *= aspect_scale" if fixed
    font size in physical screen is required.
        
    So, what we need are:
    1. do expanding
    2. fix stretched font (simply let ScaleY = Y"/Y )
    3. choose an appropriate scale of font
        
    We also want to place the subtitle as close to the picture as possible
    (instead of the very bottom of the screen, which is visual distracting).
    This can be done via the ASS style tag "MarginV", which denotes the
    relative vertical margin in the ass rendering screen (i.e. the screen
    of PlayResX:PlayResY).
        
    Another approach is just adding a black band with adequate width to contain
    the OSDs/texts, avoiding ugly "MarginV".
    """
    def __init__(self):
        self.__use_ass = True

        if not MPlayerContext().support("ass") or "-noass" in CmdLineParser().args:
            self.__use_ass = False
        else:
            # -ass option require libass with fontconfig support
            libass_path = None
            for l in subprocess.check_output(["ldd",MPlayerContext().path]).splitlines():
                if "libass" in l:
                    libass_path = l.split()[2]
            if not libass_path:
                self.__use_ass = False
            else:
                if not "libfontconfig" in subprocess.check_output(["ldd",libass_path]):
                    self.__use_ass = False

        # mplayer option "-subfont-autoscale" affects all osds/texts
        # because we will change the movie height, only mode 2 is applicable,
        # in which is proportional to movie width
        self.__scaling_mode = 2

        # text scales: make the fonts fixed size in fullscreen (independent on
        # the video size)
        self.__osd_scale = 3
        self.__text_scale = 4.5

        # ass font scale:
        if self.__use_ass:
            # recall that ass font is rendered in a 384x288 canvas; as we
            # change the height, we use the width
            self.__ass_scale = 1.5
            # 1.25 lines of 18-pixels font in screen of height 288
            self.__ass_margin_scale = self.__ass_scale * 18/288 * 1.25

    def expand(self, media):
        """Given a MediaContext, expand the video.
   
        Return the arguments list for mplayer.
        """
        screen_aspect = check_screen_dim().aspect

        # basic options
        args = "-subfont-autoscale {0} -subfont-osd-scale {1}".format(self.__scaling_mode,self.__osd_scale).split()
        
        # do expansion
        if media.DAR < Fraction(4,3):
            # if the video is too narrow (<4:3), force 4:3
            args.extend("-vf-pre dsize=4/3".split())
        elif self.__use_ass:
            # Y"/Y = X:Y / X:Y", i.e. vertical_ratio = DAR / expanded_DAR
            vertical_ratio = 1.0 + 2 * self.__ass_margin_scale
            aspect_scale = media.DAR / Fraction(4,3)
            
            if media.DAR / vertical_ratio > screen_aspect:
                aspect_scale = aspect_scale / vertical_ratio
                margin = int(self.__ass_margin_scale * media.frame.height)
                args.extend("-ass-use-margins -ass-bottom-margin {0} -ass-top-margin {0}".format(margin).split())
                if MPlayerContext().is_mplayer2:
                    args.extend("-ass-force-style ScaleY={0}".format(vertical_ratio).split())
                
            args.extend("-ass -ass-font-scale {0}".format(self.__ass_scale * aspect_scale).split());
        else:
            args.extend("-subpos 98 -subfont-text-scale {0} -vf-pre expand={1}::::1:{2}"
                        .format(self.__text_scale, media.frame.width, screen_aspect).split())
        return args
        
class UTF8Converter(object):
    ascii = ["[\x09\x0A\x0D\x20-\x7E]"]

    gbk = []
    gbk.append("[\xA1-\xA9][\xA1-\xFE]")              # Level GBK/1
    gbk.append("[\xB0-\xF7][\xA1-\xFE]")              # Level GBK/2
    gbk.append("[\x81-\xA0][\x40-\x7E\x80-\xFE]")     # Level GBK/3
    gbk.append("[\xAA-\xFE][\x40-\x7E\x80-\xA0]")     # Level GBK/4
    gbk.append("[\xA8-\xA9][\x40-\x7E\x80-\xA0]")     # Level GBK/5

    big5 = []
    big5.append("[\xA1-\xA2][\x40-\x7E\xA1-\xFE]|\xA3[\x40-\x7E\xA1-\xBF]")    # Special symbols
    big5.append("\xA3[\xC0-\xFE]")                                             # Reserved, not for user-defined characters
    big5.append("[\xA4-\xC5][\x40-\x7E\xA1-\xFE]|\xC6[\x40-\x7E]")             # Frequently used characters
    big5.append("\xC6[\xA1-\xFE]|[\xC7\xC8][\x40-\x7E\xA1-\xFE]")              # Reserved for user-defined characters
    big5.append("[\xC9-\xF8][\x40-\x7E\xA1-\xFE]|\xF9[\x40-\x7E\xA1-\xD5]")    # Less frequently used characters
    big5.append("\xF9[\xD6-\xFE]|[\xFA-\xFE][\x40-\x7E\xA1-\xFE]")             # Reserved for user-defined characters

    utf8 = []
    utf8.append("[\x09\x0A\x0D\x20-\x7E]")            # ASCII
    utf8.append("[\xC2-\xDF][\x80-\xBF]")             # non-overlong 2-byte
    utf8.append("\xE0[\xA0-\xBF][\x80-\xBF]")         # excluding overlongs
    utf8.append("[\xE1-\xEC\xEE\xEF][\x80-\xBF]{2}")  # straight 3-byte
    utf8.append("\xED[\x80-\x9F][\x80-\xBF]")         # excluding surrogates
    utf8.append("\xF0[\x90-\xBF][\x80-\xBF]{2}")      # planes 1-3
    utf8.append("[\xF1-\xF3][\x80-\xBF]{3}")          # planes 4-15
    utf8.append("\xF4[\x80-\x8F][\x80-\xBF]{2}")      # plane 16

    def guess_enc(self, s):
        # http://www.w3.org/International/questions/qa-forms-utf-8
        if len("".join(re.split("(?:"+"|".join(self.utf8)+")+",s))) < 20:
            return "utf8"
        elif len("".join(re.split("(?:"+"|".join(self.ascii+self.gbk)+")+",s))) < 20:
            return "gbk"
        elif len("".join(re.split("(?:"+"|".join(self.ascii+self.big5)+")+",s))) < 20:
            return "big5"
        else:
            return "unknown"
    def guess_enc1(self, s):
        if len("".join(re.split("(?:"+"|".join(self.utf8)+")+",s))) < 20:
            return "utf8"
        # http://www.ibiblio.org/pub/packages/ccic/software/data/chrecog.gb.html
        l = len(re.findall("[\xA1-\xFE][\x40-\x7E]",s))
        h = len(re.findall("[\xA1-\xFE][\xA1-\xFE]",s))
        if l == 0:
            return "gb2312"
        elif float(l)/float(h) < 1.0/4.0:
            return "gbk"
        else:
            return "big5"
    def convert(self, s, is_path=False):
        if is_path:
            with open(s,"rb") as f:
                ss = f.read()
        else:
            ss = s

        enc = self.guess_enc(ss)
        if enc in ["utf8","unknown"]:
            if not is_path:
                return ss
        else:
            ss = ss.decode(enc,'ignore').encode("utf8")
            if is_path:
                with open(s,"wb") as f:
                    f.write(ss)
            else:
                return ss

class PlaylistGenerator(object):
    """Generate a list for continuous playing.
    """
    def __init__(self,files):
        if not len(files)==1 or not os.path.exists(files[0]):
            self.playlist = files
        else:
            self.playlist = self.__gen(files[0])

    def __gen(self, path):
        def translate(s):
            dic = dict(zip(u'零壹贰叁肆伍陆柒捌玖〇一二三四五六七八九','01234567890123456789'))
            loc = locale.getdefaultlocale()
            s = s.decode(loc[1])
            return ''.join([dic.get(c,c) for c in s]).encode(loc[1])
        def split_by_int(s):
            return [x for x in re.split('(\d+)', translate(s)) if x != '']
        def make_sort_key(s):
            return [(int(x) if x.isdigit() else x) for x in split_by_int(s)]
        def strip_to_int(s,prefix):
            if prefix and prefix in s:
                s = s.partition(prefix)[2]
            s = split_by_int(s)[0]
            return int(s) if s.isdigit() else float('NaN')
    
        pdir, basename = os.path.split(os.path.abspath(path))

        # basic candidate filtering
        # 1. extention
        files = [f for f in os.listdir(pdir) if f.endswith(os.path.splitext(basename)[1])]
        # 2. remove previous episodes
        files.sort(key=make_sort_key)
        del files[0:files.index(basename)]

        # only one file in the candidate list
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

@singleton
class CmdLineParser:
    """ Attributes:
      role, files, args, bad_args
    """
    def __init__(self):
        args_to_parse = sys.argv[:]

        self.args = []
        self.files = []

        self.bad_args =[]

        app = os.path.basename(args_to_parse.pop(0))
        if "mplayer" in app:
            self.role = "player"
        elif "midentify" in app:
            self.role = "identifier"
            self.files = args_to_parse[:]

            args_to_parse = []
        else:
            self.role = "unknown"

        while args_to_parse:
            s = args_to_parse.pop(0)
            if s == "-debug":
                logging.root.setLevel(logging.DEBUG)
            elif s == "-dry-run":
                logging.root.setLevel(logging.DEBUG)
                global dry_run
                dry_run = True
            elif s == "--":
                self.files.extend(args_to_parse)
                args_to_parse = []
            elif s.startswith("-"):
                flag = MPlayerContext().support(s.partition('-')[2])
                if flag == 0:
                    self.bad_args.append(s)
                elif flag == 1:
                    self.args.append(s)
                elif flag == 2:
                    self.args.append(s)
                    if args_to_parse:
                        self.args.append(args_to_parse.pop(0))
            else:
                self.files.append(s)

        if self.bad_args:
            logging.info("Unsupported options \"" + " ".join(self.bad_args) + "\" are automatically suspressed.")

@singleton
class MPlayerContext(object):
    path = None
    is_mplayer2 = True
    
    def support(self, opt):
        """return value:
        0: don't support the option
        1: support and take no param
        2: support and take 1 param
        """
        if not self.__opts: self.__gen_opts()
        return self.__opts[opt] if opt in self.__opts else 0

    def __init__(self):
        self.__opts = {}
        for p in ["/opt/bin/mplayer","/usr/local/bin/mplayer","/usr/bin/mplayer"]:
            if os.path.isfile(p):
                self.path = p
                break
        if not self.path:
            raise RuntimeError,"Cannot find a mplayer binary."

    def __gen_opts(self):
        options = subprocess.Popen([self.path, "-list-options"], stdout=subprocess.PIPE).communicate()[0].splitlines()
        if options[-1].endswith('codecs'):
            logging.debug("Is not MPlayer2")
            self.is_mplayer2 = False
            options = options[3:len(options)-4]
        else:
            logging.debug("Is MPlayer2")
            self.is_mplayer2 = True
            options = options[3:len(options)-3]

        for line in options:
            s = line.split()
            opt = s[0].split(":") # take care of option:suboption
            if opt[0] in self.__opts:
                continue
            self.__opts[opt[0]] = (2 if len(opt)==2 or s[1]!="Flag" else 1)

        # handle vf* af*:
        # mplayer reports option name as vf*, which is a family of options.
        del self.__opts['af*']
        del self.__opts['vf*']
        for extra in ["af","af-adv","af-add","af-pre","af-del","vf","vf-add","vf-pre","vf-del"]:
            self.__opts[extra] = 2
        for extra in ["af-clr","vf-clr"]:
            self.__opts[extra] = 1

def parse_shooter_package(fileobj):
    subtitles = []
    f = fileobj

    # read contents
    c = f.read(1)
    package_count = struct.unpack("!b", c)[0]

    logging.info("{0} subtitle packages found".format(package_count))

    for i in range(package_count):
        c = f.read(8)
        package_length, desc_length = struct.unpack("!II", c)
        description = f.read(desc_length).decode("UTF-8")
        if not description:
            description = "no description"
        if 'delay' in description:
            sub_delay = float(description.partition("=")[2]) / 1000
        else:
            sub_delay = 0

        logging.info("Length of current package in bytes: {0}".format(package_length))

        c = f.read(5)
        package_length, file_count = struct.unpack("!IB", c)
            
        logging.info("{0} subtitles in current package ({1})".format(file_count,description))

        for j in range(file_count):
            c = f.read(8)
            pack_len, ext_len = struct.unpack("!II", c)
            ext = f.read(ext_len)
            logging.info(' subtitle format is: {0}'.format(ext))

            c = f.read(4)
            file_len = struct.unpack("!I", c)[0]
            sub = f.read(file_len)
            if sub.startswith("\x1f\x8b"):
                import gzip
                from cStringIO import StringIO
                sub = gzip.GzipFile(fileobj=StringIO(sub)).read()

            subtitles.append({'suffix': '',
                              'extension': ext,
                              'delay': sub_delay,
                              'content': sub})

    logging.info("{0} subtitle(s) fetched.".format(len(subtitles)))

    for i,s in enumerate(subtitles):
        s['suffix'] = (str(i) if i>0 else "")

    return subtitles
        
class SubFetcher(object):
    subtitles = []
    
    def fetch(self, media):
        # wait for mplayer to settle up
        time.sleep(3)

        MPlayerInstance().send("osd_show_text \"正在查询字幕...\" 5000")
        for i, t in enumerate(self.__tries):
            try:
                logging.debug("Wait for {0}s to connect to shooter server ({1})...".format(t,i))
                time.sleep(t)

                self.__build_req(media)
                response = urllib2.urlopen(self.__req)
                self.subtitles = parse_shooter_package(response)
                response.close()
                
                if self.subtitles:
                    break
                
            except urllib2.URLError, e:
                logging.debug(e)

        if self.subtitles:
            prefix = os.path.splitext(media.fullpath)[0]

            # save subtitles and generate mplayer fifo commands
            for s in self.subtitles:
                path = prefix + s['suffix'] + "." + s['extension']
                logging.info("Saving subtitle as {0}".format(path))
                with open(path,"wb") as f:
                    f.write(UTF8Converter().convert(s['content']))
                MPlayerInstance().send("sub_load \"{0}\"".format(path))
                if not s['delay'] == 0:
                    MPlayerInstance().send("sub_delay \"{0}\"".format(s['delay']))
            MPlayerInstance().send("sub_file 0")
        else:
            logging.info("Failed to fetch subtitles.")
            MPlayerInstance().send("osd_show_text \"查询字幕失败.\" 3000")
    
    def __init__(self):
        import httplib
        self.__schemas = ["http", "https"] if hasattr(httplib, 'HTTPS') else ["http"]
        self.__servers = ["www", "splayer", "svplayer"] + ["splayer"+str(i) for i in range(1,13)]

        self.__req = None
        self.__tries = [0, 10, 30, 60, 120]
        self.__fetch_successful = False

    def __fake_splayer_env(self, media):
        self.__rev = 2437                               # as of 2012-07-02
        self.__filehash = media.hash_str
        self.__pathinfo= '\\'.join(['D:',
                                    os.path.basename(os.path.dirname(media.fullpath)),
                                    os.path.basename(media.fullpath)])
        vhash_base = 'SP,aerSP,aer {0} &e(\xd7\x02 {1} {2}'.format(self.__rev,
                                                                   self.__pathinfo,
                                                                   self.__filehash)
        self.__vhash = hashlib.md5(vhash_base).hexdigest()
        
    def __build_req(self, media):
        self.__fake_splayer_env(media)

        import random
        boundary = "-"*28 + "{0:x}".format(random.getrandbits(48))

        url = "{0}://{1}.shooter.cn/api/subapi.php".format(random.choice(self.__schemas),
                                                           random.choice(self.__servers))

        header = []
        header.append(["User-Agent", "SPlayer Build {0}".format(self.__rev)])
        header.append(["Content-Type", "multipart/form-data; boundary={0}".format(boundary)])

        items = []
        items.append(["filehash", self.__filehash])
        items.append(["pathinfo", self.__pathinfo])
#        items.append(["lang", "eng"])
        items.append(["vhash", self.__vhash])
        
        data = ''.join(["--{0}\n"
                        "Content-Disposition: form-data; name=\"{1}\"\n\n"
                        "{2}\n".format(boundary, d[0], d[1]) for d in items]
                       + ["--" + boundary + "--"])

        logging.debug("Querying server {0} with\n"
                      "{1}\n"
                      "{2}\n".format(url,header,data))

        self.__req = urllib2.Request(url)
        for h in header:
            self.__req.add_header(h[0],h[1])
        self.__req.add_data(data)
    
@singleton
class MPlayerInstance(object):
    last_timestamp = 0.0
    last_exit_status = None

    def send(self,cmd):
        if self.__process:
            logging.debug("Sending command \"{0}\" to {1}...".format(cmd, Fifo().path))
            fifo = open(Fifo().path,"w")
            fifo.write(cmd+'\n')
            fifo.close()
        else:
            logging.debug("Command \"{0}\" discarded.".format(cmd))

    def identify(self, filelist):
        args = [MPlayerContext().path] +"-vo null -ao null -frames 0 -identify".split() + filelist
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        return [l for l in p.communicate()[0].splitlines() if l.startswith("ID_")]

    def play(self, filename=[]):
        media = MediaContext(filename)
        if not dry_run:
            self.__process = subprocess.Popen(media.args, stdin=sys.stdin, stdout=subprocess.PIPE, stderr=None)
            self.__tee()
            logging.debug("Last timestamp: {0}".format(self.last_timestamp))
            logging.debug("Last exit status: {0}".format(self.last_exit_status))
            self.__process = None
        
    def __tee(self):
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
                self.__flush_first_line(f,lines)
            elif c == '\r':
                d = p.stdout.read(1)
                if d == '\n':
                    lines[4].append('\n')
                    self.__flush_first_line(f,lines)
                else:
                    self.__flush_first_line(f,lines)
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

    def __flush_first_line(self, fileobj, lines):
        fileobj.write(''.join(lines.pop(0)))
        fileobj.flush()
        lines.append([])
        
    def __init__(self):
        self.__process = None

class MediaContext(object):
    """Construct media metadata and args for mplayer
    """
    def __init__(self, path):
        """Parse the output of midentify.
        """
        self.filename = path
        self.fullpath = path
        self.seekable = False
        self.is_video = False
        self.args = [MPlayerContext().path]

        # nothing has to do
        if not path: return
        
        self.__fetch_thread = None
        self.__subtitle_keys = ["ID_SUBTITLE_ID", "ID_FILE_SUB_ID",       "ID_VOBSUB_ID",
                                                  "ID_FILE_SUB_FILENAME", "ID_VOBSUB_FILENAME"]

        info = {}
        for l in MPlayerInstance().identify([path]):
            a = l.partition("=")
            if a[0] in self.__subtitle_keys:
                if not a[0] in info:
                    info[a[0]] = []

                info[a[0]].append(a[2])
            else:
                info[a[0]] = a[2]

        if "ID_FILENAME" in info:
            self.__gen_meta_info(info)
        
            if self.is_video:
                self.__gen_video_info(info)
                self.__gen_subtitle_info(info)

                self.args.extend(VideoExpander().expand(self))
                if not dry_run and not "text" in "".join(self.subtitle_types):
                    self.__fetch_thread = threading.Thread(target=SubFetcher().fetch, args=(self,))
                    self.__fetch_thread.daemon = True
                    self.__fetch_thread.start()

                self.args.extend("-subcp utf8".split())
                self.args.extend(Fifo().args)

        self.args.extend(CmdLineParser().args)
        self.args.append(self.filename)

        self.__log()

    def __log(self):
        items = ["\n"
                 "  Fullpath:         {0}\n"
                 "  Seekable:         {1}\n"
                 "  Video:            {2}\n".format(self.fullpath, self.seekable, self.is_video)]
        if self.is_video:
            items.append("    Dimension:      {0} [SAR {1} DAR {2}]\n"
                         "    Hash string:    {3}\n"
                         "    Subtitles:      {4}\n"
                         .format("{0.width}x{0.height}".format(self.frame),
                                 '{0.numerator}:{0.denominator}'.format(self.SAR),
                                 '{0.numerator}:{0.denominator}'.format(self.DAR),
                                 self.hash_str,
                                 "\n                    ".join(self.subtitles)))
        items.append("{0}".format(" ".join(self.args)))
        logging.debug(''.join(items))

    def __gen_meta_info(self,info):
        self.filename = info["ID_FILENAME"]
        self.fullpath = os.path.realpath(self.filename)
        self.seekable = (info["ID_SEEKABLE"] == "1")
        self.is_video = True if "ID_VIDEO_ID" in info else False
        
    def __gen_video_info(self,info):
        # Aspect Ratios and Frame Sizes
        # reference: http://www.mir.com/DMG/aspect.html
        self.frame = Dimension(info["ID_VIDEO_WIDTH"], info["ID_VIDEO_HEIGHT"])
        if "ID_VIDEO_ASPECT" in info:
            self.DAR = Fraction(info["ID_VIDEO_ASPECT"]).limit_denominator(10)
        else:
            self.DAR = self.frame.aspect
        self.SAR = (self.DAR / self.frame.aspect).limit_denominator(82)

        # Unique (hopefully) hash for shooter subtitle search
        sz = os.path.getsize(self.fullpath)
        if sz>8192:
            with open(self.fullpath, 'rb') as f:
                self.hash_str = ';'.join([(f.seek(s), hashlib.md5(f.read(4096)).hexdigest())[1] for s in (lambda l:[4096, l/3*2, l/3, l-8192])(sz)])
        else:
            self.hash_str = ';;;'
            
    def __gen_subtitle_info(self,info):
        self.subtitle_types = []
        self.subtitles = []
        if "ID_SUBTITLE_ID" in info:
            self.subtitle_types.append("embedded text")
            self.subtitles.extend(["(embedded text)"])
        if "ID_FILE_SUB_ID" in info:
            self.subtitle_types.append("external text")
            self.subtitles.extend(info["ID_FILE_SUB_FILENAME"])
            if not dry_run:
                logging.debug("Trying coverting subtitles to UTF-8...")
                for p in info["ID_FILE_SUB_FILENAME"]:
                    UTF8Converter().convert(p,True)
        if "ID_VOBSUB_ID" in info:
            self.subtitle_types.append("external vobsub")
#            self.subtitles.extend(info["ID_VOBSUB_FILENAME"])

def run():
    if CmdLineParser().role == "identifier":
        print '\n'.join(MPlayerInstance().identify(CmdLineParser().files))
    elif CmdLineParser().role == "player":
        files = PlaylistGenerator(CmdLineParser().files).playlist

        if not files:
            MPlayerInstance().play()
        else:
            for f in files:
                MPlayerInstance().play(f)
                if MPlayerInstance().last_exit_status == "Quit":
                    break
            
if __name__ == "__main__":
    dry_run = False
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)

    if sys.hexversion < 0x02070000:
        logging.info("Please run the script with python>=2.7.0")
    else:
        run()
#        f = open('/home/subi/mi_sub.pkg')
#        sub = parse_shooter_package(f)
#        print sub[0]['content']
        
