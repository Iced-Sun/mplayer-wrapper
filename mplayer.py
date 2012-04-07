#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2012 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <subi 2012/04/07 23:08:30>
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
# * IPCPipe need reconsidering
# * detect the language in embedded subtitles, which is guaranteed to be utf8
# * use ffprobe for better(?) metainfo detection?

import logging
import os, sys, time
import struct, urllib2
import locale,re
import multiprocessing, subprocess
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
    """Mimic shell command "which".
    """
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

class DimensionChecker(object):
    def __init__(self):
        """Select the maximal available screen dimension.
        """
        dim = [640, 480]
        if which("xrandr"):
            for l in subprocess.check_output(["xrandr"]).splitlines():
                if l.startswith("*"):
                    # xrandr 1.1
                    dim[0] = int(l.split()[1])
                    dim[1] = int(l.split()[3])
                    break
                elif '*' in l:
                    d = l.split()[0].split('x')
                    if d[0] > dim[0]:
                        dim = map(int,d)

        self.dim = dim + [Fraction(dim[0],dim[1])]

class VideoExpander(object):
    """Video expanding attaches two black bands to the top and bottom of the video.
    MPlayer will then render osds (subtitles etc.) within the bands.
        
    Two ways exist:
    1. -vf expand:
       everything done by mplayer, not compatible with libass (subtitle overlapping
       problem). Have to use the old plain subtitle renderer (-noass).
    2. -ass-use-margin:
       everything done by YOU, including the calculation of the margin heights and
       the font scales. The benefit is you can use "-ass".
        
    The "ass-use-margin" method has a very annoying problem: the subtitle characters
    are horizontally stretched. We need a fix.
        
    A wild guess for what is done in the ass renderer of mplayer/libass is taken
    after some googling and experiments:
    1. there are 3 different dimensions: the video, the display, and the ass rendering
       screen (PlayResX:PlayResY)
    2. the font scale is caculated w.r.t. the ASS styles of "PlayResX, PlayResY, ScaleX,
       ScaleY" and the mplayer option "-ass-font-scale":
       a. PlayResY defaults to 288
       b. PlayResX defaults to PlayResY/1.3333
       c. ScaleX, ScaleY both defaults to 1
       d. the final font size rendered in the VIDEO is:
               scale_base = video_Y/PlayResY * ass_font_scale 
               font_Y = scale_base * ScaleY
               font_X = scale_base * ScaleX
    3. the font size rendered in the DISPLAY is:
               scale_base_disp = disp_Y/video_Y * scale_base
                               = disp_Y/PlayResY * ass_font_scale
               font_Y_disp = scale_base_disp * ScaleY
               font_X_disp = scale_base_disp * ScaleX
        
    This is why the subtitle is alway of the same size with the same display size for
    different videos. (disp_Y/PlayResY is constant when PlayResY takes the default
    value.)
        
    While the video expanding (in Y axis) is concerned, the situation becomes a little
    messy:
             ex_scale_base = ex_video_Y/PlayResY * ass_font_scale
             ex_font_Y = (video_Y/ex_video_Y) * ex_scale_base * ScaleY
                       = font_Y
             ex_font_X = ex_scale_base * ScaleX
                       = ex_video_Y/PlayResY * ass_font_scale * ScaleX
                       = (ex_video_Y/video_Y) * font_X
    Clearly the subtitle is horizontally stretched (vertically unchanged).
        
    So, what we need is:
    1. do expanding (easy)
    2. make font be of correct aspect (simply let ScaleX = video_Y/ex_video_Y )
        
    Additionally, we also want to place the subtitle as close to the picture as
    possible (instead of the bottom of the screen, which is visual distracting).
    This can be done via the ASS style tag "MarginV", which is the relative
    vertical margin in the ass rendering screen (i.e. the screan of
    PlayResX:PlayResY).
        
    Another approach is just adding a black band that is wide enough to contain
    the subtitles, avoiding the use of "MarginV".
    """
    def expand(self, media):
        """Given a MediaContext, expand the video.
   
        Return the arguments list for mplayer.
        """
        display_aspect = DimensionChecker().dim[2]
        
        # -subfont-autoscale affects the osd, the plain old subtitle renderer
        # AND the ass subtitle renderer
        args = "-subfont-autoscale 2".split()
        
        # make the osd be of fixed size when in fullscreen, independent on video
        # size
        subfont_osd_scale = 3
        args.extend("-subfont-osd-scale {0}".format(subfont_osd_scale).split())

        if media.disp_dim[2] < Fraction(4,3):
            # assume video never narrow than 4:3
            args.extend("-vf-pre dsize=4/3".split())
        elif self.__use_ass:
            # match the subfont_text_scale
            ass_font_scale = subfont_osd_scale / 2.0
            args.extend("-ass -ass-font-scale {0}".format(ass_font_scale).split());

            # 1.25 lines of subtitles
            band_height_in_video = int(18*1.25 * ass_font_scale * media.disp_dim[1]/288)
            target_aspect = Fraction(media.disp_dim[0], media.disp_dim[1]+band_height_in_video*2)
            if target_aspect < display_aspect:
                target_aspect = display_aspect

            # expand_video_y:video_Y = (video_X/video_Y):(video_X/expanded_video_Y)
            m2t = media.disp_dim[2] / target_aspect
            if m2t > 1:
                margin = (m2t - 1) * media.disp_dim[1] / 2
                # add margin
                args.extend("-ass-use-margins -ass-bottom-margin {0} -ass-top-margin {0}".format(int(margin)).split())
                # fix stretched sutitles
                args.extend("-ass-force-style ScaleX={0}".format(1/float(m2t)).split())
        else:
            # -vf expand does its own non-square pixel adjustment; use media.pix_dim
            subfont_text_scale = subfont_osd_scale * 1.5
            args.extend("-subpos 98 -subfont-text-scale {0} -vf-pre expand={1}::::1:{2}"
                        .format(subfont_text_scale, media.pix_dim[0], display_aspect).split())
        return args
        
    def __init__(self):
        self.__use_ass = True

        if not MPlayerContext().support("ass") or "-noass" in CmdLineParser().args:
            self.__use_ass = False
        else:
            libass_path = None
            for l in subprocess.check_output(["ldd",MPlayerContext().path]).splitlines():
                if "libass" in l:
                    libass_path = l.split()[2]
            if not libass_path:
                self.__use_ass = False
            else:
                if not "libfontconfig" in subprocess.check_output(["ldd",libass_path]):
                    self.__use_ass = False

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
                with open(obj,"wb") as f:
                    f.write(ss)
            else:
                return ss

class SubFetcher(object):
    subtitles = []
    
    __req = None

    __schemas = []
    __servers = []

    __try_times = [0, 5, 10, 30, 120]
    __need_retry = False

    def fetch(self, media):
        # wait for mplayer to settle up
        time.sleep(3)

        IPCPipe().send("osd_show_text \"正在查询字幕...\" 5000")
        for i, t in enumerate(self.__try_times):
            try:
                logging.debug("Wait for {0}s to connect to shooter server ({1})...".format(t,i))
                time.sleep(t)

                self.__build_req(media)
                response = urllib2.urlopen(self.__req)
                self.__parse_response(response)

                if not self.__need_retry:
                    break
            except urllib2.URLError:
                pass

        if self.subtitles:
            prefix = os.path.splitext(media.fullpath)[0]

            # save subtitles and generate mplayer fifo commands
            for s in self.subtitles:
                path = prefix + s['suffix'] + "." + s['extension']
                logging.info("Saving subtitle as {0}".format(path))
                with open(path,"wb") as f:
                    f.write(UTF8Converter().convert(s['content']))
                IPCPipe().send("sub_load \"{0}\"".format(path))
                if not s['delay'] == 0:
                    IPCPipe().send("sub_delay \"{0}\"".format(s['delay']))
            IPCPipe().send("sub_file 0")
        else:
            logging.info("Failed to fetch subtitles.")
            IPCPipe().send("osd_show_text \"查询字幕失败.\" 3000")
            
    def __init__(self):
        import httplib
        self.__schemas = ["http", "https"] if hasattr(httplib, 'HTTPS') else ["http"]
        self.__servers = ["www", "splayer"] + ["splayer"+str(i) for i in range(1,13)]

    def __parse_response(self, response):
        c = response.read(1)
        package_count = struct.unpack("!b", c)[0]

        logging.info("{0} subtitle packages found".format(package_count))

        for i in range(package_count):
            c = response.read(8)
            package_length, desc_length = struct.unpack("!II", c)
            description = response.read(desc_length).decode("UTF-8")
            if not description:
                description = "no description"
            if 'delay' in description:
                sub_delay = float(description.partition("=")[2]) / 1000
            else:
                sub_delay = 0

            logging.info("Length of current package in bytes: {0}".format(package_length))

            c = response.read(5)
            package_length, file_count = struct.unpack("!IB", c)
            
            logging.info("{0} subtitles in current package ({1})".format(file_count,description))

            for j in range(file_count):
                c = response.read(8)
                pack_len, ext_len = struct.unpack("!II", c)
                ext = response.read(ext_len)

                c = response.read(4)
                file_len = struct.unpack("!I", c)[0]
                sub = response.read(file_len)

                if sub.startswith("\x1f\x8b"):
                    import gzip
                    from cStringIO import StringIO
                    self.subtitles.append({'suffix': "",
                                           'extension': ext,
                                           'delay': sub_delay,
                                           'content': gzip.GzipFile(fileobj=StringIO(sub)).read()})
                else:
                    logging.warning("Unknown format or incomplete data. Try again later...")
                    self.__need_retry = True

        logging.info("{0} subtitle(s) fetched.".format(len(self.subtitles)))

        for i,s in enumerate(self.subtitles):
            s['suffix'] = (str(i) if i>0 else "")

    def __build_req(self, media):
        import random
        boundary = "-"*28 + "{0:x}".format(random.getrandbits(48))

        url = "{0}://{1}.shooter.cn/api/subapi.php".format(random.choice(self.__schemas),
                                                           random.choice(self.__servers))

        header = []
        header.append(["User-Agent", "SPlayer Build ${0}".format(random.randint(1217,1543))])
        header.append(["Content-Type", "multipart/form-data; boundary={0}".format(boundary)])

        items = []
        items.append(["pathinfo", os.path.join("c:/",
                                               os.path.basename(os.path.dirname(media.fullpath)),
                                               os.path.basename(media.fullpath))])
        items.append(["filehash", media.hash_str])
        items.append(["lang", "chn"])

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

        if self.role == "player":
            self.files = PlaylistGenerator(self.files).playlist
        
@singleton
class MPlayerContext(object):
    path = None
    
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
        options = options[3:len(options)-3]

        for line in options:
            s = line.split();
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

@singleton
class MPlayerInstance(object):
    last_timestamp = 0.0
    last_exit_status = None

    def identify(self, filelist):
        args = [MPlayerContext().path] +"-vo null -ao null -frames 0 -identify".split() + filelist
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        return [l for l in p.communicate()[0].splitlines() if l.startswith("ID_")]

    def play(self, media=[]):
        def tee(f=sys.stdout):
            def flush(f,lines):
                f.write(''.join(lines.pop(0)))
                f.flush()
                lines.append([])

            p = self.__process
            # cache 5 lines in case of unexpected outputs
            lines = [[] for i in range(5)]
            while True:
                c = p.stdout.read(1)
                lines[4].append(c)
                if c == '\n':
                    flush(f,lines)
                elif c == '\r':
                    d = p.stdout.read(1)
                    if d == '\n':
                        lines[4].append('\n')
                        flush(f,lines)
                    else:
                        flush(f,lines)
                        lines[4].append(d)
                elif c == '':
                    break
            for l in (''.join(ll) for ll in lines):
                if l.startswith(('A:','V:')):
                    self.last_timestamp = float(l[2:9])
                if l.startswith('Exiting...'):
                    self.last_exit_status = l[12:len(l)-2]
                f.write(l)
                f.flush()

        if dry_run:
            return

        self.__process = subprocess.Popen(media.args, stdin=sys.stdin, stdout=subprocess.PIPE, stderr=None)
        tee()

        logging.debug("Last timestamp: {0}".format(self.last_timestamp))
        logging.debug("Last exit status: {0}".format(self.last_exit_status))

    def __init__(self):
        pass

class MediaContext:
    """Construct media metadata and args for mplayer
    """
    exist = True

    filename = ""
    fullpath = ""

    seekable = True
    is_video = False

    args = []
    
    hash_str = ""

    subtitle_types = []
    subtitles = []

    def destory(self):
        if self.__proc_fetcher and self.__proc_fetcher.is_alive():
            logging.info("Terminating subtitle fetching...")
            self.__proc_fetcher.terminate()
    
    def __init__(self, path):
        """Parse the output by midentify.
        """
        self.filename = path
        self.args = [MPlayerContext().path] + "-subcp utf8".split() + Fifo().args

        self.__proc_fetcher = None
        self.__subtitle_keys = ["ID_SUBTITLE_ID",       "ID_FILE_SUB_ID",       "ID_VOBSUB_ID",
                                "ID_SUBTITLE_FILENAME", "ID_FILE_SUB_FILENAME", "ID_VOBSUB_FILENAME"]

        info = {}
        for l in MPlayerInstance().identify([path]):
            a = l.partition("=")
            if a[0] in self.__subtitle_keys:
                if not a[0] in info:
                    info[a[0]] = []

                info[a[0]].append(a[2])
            else:
                info[a[0]] = a[2]

        if not "ID_FILENAME" in info:
            self.exist = False;
            return

        self.__gen_meta_info(info)
        
        if self.is_video:
            self.__gen_video_info(info)
            self.__gen_subtitle_info(info)

            self.args.extend(VideoExpander().expand(self))
            if not dry_run and not "text" in "".join(self.subtitle_types):
                sub = SubFetcher()
                self.__proc_fetcher = multiprocessing.Process(target=sub.fetch, args=(self,))
                self.__proc_fetcher.start()

        self.args.extend(CmdLineParser().args)
        self.args.append(self.filename)

        self.__log()

    def __log(self):
        items = ["\n"
                 "  Fullpath:         {0}\n"
                 "  Seekable:         {1}\n"
                 "  Video:            {2}\n".format(self.fullpath, self.seekable, self.is_video)]
        
        if self.is_video:
            items.append("    Dim(pixel):     {0} @ {1}\n"
                         "    Dim(disp):      {2} @ {3}\n"
                         "    Hash string:    {4}\n"
                         "    Subtitles:      {5}\n".format("{0[0]}x{0[1]}".format(self.pix_dim),  self.pix_dim[2],
                                                            "{0[0]}x{0[1]}".format(self.disp_dim), self.disp_dim[2],
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
        self.pix_dim = [int(info["ID_VIDEO_WIDTH"]), int(info["ID_VIDEO_HEIGHT"]), 0]
        if "ID_VIDEO_ASPECT" in info:
            self.pix_dim[2] = Fraction(info["ID_VIDEO_ASPECT"]).limit_denominator(10)
        else:
            self.pix_dim[2] = Fraction(self.pix_dim[0],self.pix_dim[1])
        # non-square pixel, i.e. w:h != aspect
        # used for correct calculation in video expanding
        self.disp_dim = self.pix_dim[:]
        if self.disp_dim[2] == 0:
            self.disp_dim[2] = Fraction(self.disp_dim[0],self.disp_dim[1])
        if abs(Fraction(self.disp_dim[0],self.disp_dim[1]) - self.disp_dim[2]) > 0.1:
            self.disp_dim[0] = int(round(self.disp_dim[1] * self.disp_dim[2]))

        sz = os.path.getsize(self.fullpath)
        if sz>8192:
            with open(self.fullpath, 'rb') as f:
                self.hash_str = ';'.join([(f.seek(s), hashlib.md5(f.read(4096)).hexdigest())[1] for s in (lambda l:[4096, l/3*2, l/3, l-8192])(sz)])
            
    def __gen_subtitle_info(self,info):
        if "ID_SUBTITLE_ID" in info:
            self.subtitle_types.append("embedded text")
#            self.subtitles.extend(info["ID_SUBTITLE_FILENAME"])
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

@singleton
class IPCPipe(object):
    def send(self, cmd):
        if isinstance(cmd, str):
            self.writer.send([cmd])
        else:
            self.writer.send(cmd)
    
    def terminate(self):
        if not dry_run:
            self.__proc_listener.terminate()

    def __init__(self):
        def listen(fifo_path):
            """Run in another process. how could pass the MPlayer subprocess to it?
            """
            def send_cmd(c):
                # FIXME
                if True: #mplayer.is_alive():
                    logging.debug("Sending command \"{0}\" to {1}".format(c, fifo_path))
                    fifo = open(fifo_path,"w")
                    fifo.write(c+'\n')
                    fifo.close()

            while True:
                if self.reader.poll():
                    for c in self.reader.recv():
                        send_cmd(c)
                else:
                    time.sleep(2)

        if not dry_run:
            self.reader, self.writer = multiprocessing.Pipe(False)

            self.__proc_listener = multiprocessing.Process(target=listen, args=(Fifo().path,))
            self.__proc_listener.start()

@singleton
class Fifo:
    def __init__(self):
        import tempfile
        self.__tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.__tmpdir, "mplayer_fifo")
        self.args = "-input file={0}".format(self.path).split()            
        os.mkfifo(self.path)
    def __del__(self):
        os.unlink(self.path)
        os.rmdir(self.__tmpdir)

def run():
    if CmdLineParser().role == "identifier":
        print '\n'.join(MPlayerInstance().identify(CmdLineParser().files))
    elif CmdLineParser().role == "player":
        if not CmdLineParser().files:
            MPlayerInstance().play()
        else:
            IPCPipe()
            for f in CmdLineParser().files:
                media = MediaContext(f)
                MPlayerInstance().play(media)
                media.destory()

                if MPlayerInstance().last_exit_status == "Quit":
                    break
            IPCPipe().terminate()
            
if __name__ == "__main__":
    dry_run = False
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)

    if sys.hexversion < 0x02070000:
        logging.info("Please run the script with python>=2.7.0")
    else:
        run()
