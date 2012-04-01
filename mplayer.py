#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2012 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <subi 2012/04/01 13:08:41>
#
# mplayer-wrapper is an MPlayer frontend, trying to be a transparent interface.
# It is convenient to rename the script to "mplayer" and place it in your $PATH
# (don't overwrite the real MPlayer); you would not even notice its existence.

# TODO:
# 1. data persistance (aka data cache) for
#    i)   resume last played position
#    ii)  remember last settings (volume/hue/contrast etc.)
#    iii) dedicated dir for subtitles
#    iv)  MPlayerContext
# 2. remember last volume/hue/contrast for continuous playing (don't need data
#    persistance)
# 3. shooter sometimes return a false subtitle with the same time length. find a
#    cure. (using zenity, pygtk, or both?)
# 4. filehash should be in Media
# 5. xset s off
# 6. retry on failure of fetching
# 7. proper subcp handling
# 8. give some visual feedback when failing to fetch subtitles
# 9: "not compiled in option"
# 10: IPCPipe need reconsidering
# 11: also convert on-disk subtitles to utf8 and add "-subcp utf8"

import logging
import os, sys, time
import struct, urllib2
import locale,re
import multiprocessing, subprocess
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
            if fullpath: return fullpath
    return None

class DimensionChecker(object):
    def __init__(self):
        """Select the maximal available screen dimension.
        """
        dim = [640, 480]
        if which("xrandr"):
            p = subprocess.Popen(["xrandr"], stdout = subprocess.PIPE)
            for line in p.communicate()[0].splitlines():
                if line.startswith("*"):
                    # xrandr 1.1
                    dim[0] = int(line.split()[1])
                    dim[1] = int(line.split()[3])
                    break
                elif '*' in line:
                    d = line.split()[0].split('x')
                    if d[0] > dim[0]: dim = map(int,d)

        self.dim = dim + [Fraction(dim[0],dim[1])]

@singleton
class VideoExpander(object):
    """Given a MediaContext "m", expand the video to the display_aspect with
    "method".

    Return the arguments list for mplayer.

    Video expanding attaches two black bands to the top and bottom of the video.
    MPlayer will then render osds (subtitles etc.) within the bands.
        
    Two ways exist:
    1. -vf expand:
       everything done by mplayer, not compatible with libass (subtitle overlapping
       problem). Have to use the old plain subtitle renderer (-noass).
    2. -ass-use-margin:
       everything done by YOU, including the calculation of the margin heights and
       the font scales. The benefit is you can use "-ass".
        
    The "ass-use-margin" method has a very annoying problem: the subtitle charecters
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
        
    Addtionally, we also want to place the subtitle as close to the picture as
    possible (instead of the bottom of the screen, which is visual distracting).
    This can be done via the ASS style tag "MarginV", which is the relative relative
    in the ass rendering screen (i.e. the screan of PlayResX:PlayResY).
        
    Another approach is just adding a black band that is wide enough to contain
    the subtitles, avoiding the use of "MarginV".
    """
    def expand(self, media):
        display_aspect = DimensionChecker().dim[2]
        
        # -subfont-autoscale has nothing to do with libass, only affect the osd and
        # the plain old subtitle renderer
        args = "-subfont-autoscale 2".split()

        if media.scaled_dimension[2] < Fraction(4,3):
            # assume we will never face a narrower screen than 4:3
            args.extend("-vf-pre dsize=4/3".split())
        elif self.__use_ass:
            # feel free to change the scale for your favor.
            ass_font_scale = 2
            args.extend("-ass -ass-font-scale {0}".format(ass_font_scale).split());

            subtitle_height_in_video = int(18*1.25/288 * ass_font_scale * media.scaled_dimension[1])
            target_aspect = Fraction(media.scaled_dimension[0], media.scaled_dimension[1]+subtitle_height_in_video*2)
            if target_aspect < display_aspect:
                target_aspect = display_aspect

            # expand_video_y:video_Y = (video_X/video_Y):(video_X/expanded_video_Y)
            m2t = media.scaled_dimension[2] / target_aspect
        
            if m2t > 1:
                margin = (m2t - 1) * media.scaled_dimension[1] / 2
                args.extend("-ass-use-margins -ass-bottom-margin {0} -ass-top-margin {0}".format(int(margin)).split())
                args.extend("-ass-force-style ScaleX={0}".format(1/float(m2t)).split())
        else:
            # -vf expand does its own non-square pixel adjustment;
            # m.original_dimension is fine
            args.extend("-subpos 98 -vf-pre expand={0}::::1:{1}"
                        .format(media.original_dimension[0], display_aspect).split())
        return args
        
    def __init__(self):
        self.__use_ass = True

        if not MPlayerContext().support("ass") or "-noass" in CmdLineParser().args:
            self.__use_ass = False
        else:
            libass_path = None
            p = subprocess.Popen(["ldd",MPlayerContext().path], stdout=subprocess.PIPE)
            for l in p.communicate()[0].splitlines():
                if "libass" in l:
                    libass_path = l.split()[2]
            if not libass_path:
                self.__use_ass = False
            else:
                p = subprocess.Popen(["ldd",libass_path], stdout=subprocess.PIPE)
                if not "libfontconfig" in p.communicate()[0]:
                    self.__use_ass = False

def convert2utf8(s):
    def guess_enc(s):
        # http://www.w3.org/International/questions/qa-forms-utf-8
        # http://www.ibiblio.org/pub/packages/ccic/software/data/chrecog.gb.html
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
        
        if len("".join(re.split("(?:"+"|".join(utf8)+")+",s))) < 20:
            return "utf8"
        elif len("".join(re.split("(?:"+"|".join(ascii+gbk)+")+",s))) < 20:
            return "gbk"
        elif len("".join(re.split("(?:"+"|".join(ascii+big5)+")+",s))) < 20:
            return "big5"
        else:
            return "unknown"

        ## another method to test gb2312 or big5
        l = len(re.findall("[\xA1-\xFE][\x40-\x7E]",s))
        h = len(re.findall("[\xA1-\xFE][\xA1-\xFE]",s))

        if l == 0:
            return "gb2312"
        elif float(l)/float(h) < 1.0/4.0:
            return "gbk"
        else:
            return "big5"
                           
    enc = guess_enc(s)
    if enc in ["utf8","unknown"]:
        return s
    else:
        return s.decode(enc,'ignore').encode("utf8")

def handle_shooter_subtitles(media, cmd_conn_write_end):
    def build_req(m):
        def hashing(path):
            sz = os.path.getsize(path)
            if sz>8192:
                import hashlib
                f = open(path, 'rb')
                filehash = ';'.join([(f.seek(s), hashlib.md5(f.read(4096)).hexdigest())[1] for s in (lambda l:[4096, l/3*2, l/3, l-8192])(sz)])
                f.close()
            else:
                filehash = ""
            return filehash

        import httplib
        schemas = ["http", "https"] if hasattr(httplib, 'HTTPS') else ["http"]
        servers = ["www", "splayer"] + ["splayer"+str(i) for i in range(1,13)]

        import random
        boundary = "-"*28 + "{0:x}".format(random.getrandbits(48))

        url = "{0}://{1}.shooter.cn/api/subapi.php".format(random.choice(schemas), random.choice(servers))

        header = []
        header.append(["User-Agent", "SPlayer Build ${0}".format(random.randint(1217,1543))])
        header.append(["Content-Type", "multipart/form-data; boundary={0}".format(boundary)])

        items = []
        items.append(["pathinfo", os.path.join("c:/",
                                               os.path.basename(os.path.dirname(m.fullpath)),
                                               os.path.basename(m.fullpath))])
        items.append(["filehash", hashing(m.fullpath)])
#        data.append(["lang", "chn"])

        data = ''.join(["--{0}\n"
                        "Content-Disposition: form-data; name=\"{1}\"\n\n"
                        "{2}\n".format(boundary, d[0], d[1]) for d in items]
                       + ["--" + boundary + "--"])

        logging.debug("Querying server {0} with\n"
                      "{1}\n"
                      "{2}\n".format(url,header,data))

        req = urllib2.Request(url)
        for h in header:
            req.add_header(h[0],h[1])
        req.add_data(data)

        return req

    def fetch_sub(req):
        def parse_package(response):
            c = response.read(8)
            package_length, desc_length = struct.unpack("!II", c)
            description = response.read(desc_length).decode("UTF-8")
            if not description: description = "no description"

            logging.info("Length of current package in bytes: {0}".format(package_length))

            c = response.read(5)
            package_length, file_count = struct.unpack("!IB", c)

            logging.info("{0} subtitles in current package ({1})".format(file_count,description))

            subs_in_pack = []
            for j in range(file_count):
                sub = parse_file(response)
                if sub: subs_in_pack.append(sub)
            return subs_in_pack

        def parse_file(response):
            c = response.read(8)
            pack_len, ext_len = struct.unpack("!II", c)
            ext = response.read(ext_len)

            c = response.read(4)
            file_len = struct.unpack("!I", c)[0]
            sub = response.read(file_len)

            if sub.startswith("\x1f\x8b"):
                import gzip
                from cStringIO import StringIO
                return [ext, gzip.GzipFile(fileobj=StringIO(sub)).read()]
            else:
                logging.warning("Unknown format or incomplete data. Trying again...")
                return None

        ### function body
        response = urllib2.urlopen(req)

        c = response.read(1)
        package_count = struct.unpack("!b", c)[0]

        logging.info("{0} subtitle packages found".format(package_count))

        subs = []
        for i in range(package_count):
            subs.extend(parse_package(response))
        logging.info("{0} subtitle(s) fetched.".format(len(subs)))

        # todo: enumerate?
        for i in range(len(subs)):
            suffix = str(i) if i>0 else ""
            subs[i][0] = suffix + '.' + subs[i][0]

        return subs

    ### function body
    # wait for mplayer to settle up
    time.sleep(3)

    IPCPipe().send(["osd_show_text \"正在查询字幕...\" 5000"])
    logging.info("Connecting to shooter server...")
    subs = fetch_sub(build_req(media))

    prefix = os.path.splitext(media.fullpath)[0]

    # save subtitles and generate mplayer fifo commands
    cmds = []
    enca = which("enca")
    for sub in subs:
        path = prefix + sub[0]
        logging.info("Saving subtitle as {0}".format(path))
        f = open(path,"wb")
        f.write(convert2utf8(sub[1]))
        f.close()
        cmds.append("sub_load \"{0}\"".format(path))
    if subs:
        cmds.append("sub_file 0")

#    IPCPipe().send(cmds)
    cmd_conn_write_end.send(cmds)

#todo: class again
class SubFetcher(object):
    pass

class PlaylistGenerator(object):
    """For the given path, generate a file list for continuous playing.
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
            self.files = self.__args_to_parse[:]

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
                flag = MPlayerContext().support(s.split('-',1)[1])
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

        args = [MPlayerContext().path]
        if media:
            args += media.args + [media.filename]
        args += CmdLineParser().args
            
        logging.debug("Final command:\n{0}".format(' '.join(args)))

        if dry_run:
            return

        self.__process = subprocess.Popen(args, stdin=sys.stdin, stdout=subprocess.PIPE, stderr=None)
        tee()

        logging.debug("Last timestamp: {0}".format(self.last_timestamp))
        logging.debug("Last exit status: {0}".format(self.last_exit_status))

    def __init__(self):
        pass

class MediaContext:
    """Construct media metadata and args for mplayer; may apply "proper" fixes
    """
    exist = True

    filename = ""
    fullpath = ""
    
    seekable = True
    is_video = False

    args = []
    
    original_dimension = [0,0,Fraction(0)]
    scaled_dimension = [0,0,Fraction(0)]

    subtitle_had = "none"

    def destory(self):
        if self.__proc_fetcher and self.__proc_fetcher.is_alive():
            logging.info("Terminating subtitle fetching...")
            self.__proc_fetcher.terminate()
    
    def __init__(self, path):
        """Parse the output by midentify.
        """
        self.filename = path
        self.__proc_fetcher = None

        info = {}
        for l in MPlayerInstance().identify([path]):
            a = l.split('=')
            info[a[0]] = a[1]

        if not "ID_FILENAME" in info:
            self.exist = False;
            return

        self.__gen_meta_info(info)
        
        if self.is_video:
            self.__gen_video_info(info)
            self.__gen_subtitle_info(info)

            self.args = VideoExpander().expand(self) + "-input file={0}".format(Fifo().path).split()
            if not dry_run and not self.subtitle_had.endswith("text"):
                self.__proc_fetcher = multiprocessing.Process(target=handle_shooter_subtitles, args=(self, IPCPipe().writer))
                self.__proc_fetcher.start()
            
        self.__log()

    def __log(self):
        items = ["{0}\n"
                 "  Fullpath:         {1}\n"
                 "  Seekable:         {2}\n"
                 "  Video:            {3}\n".format(self.filename, self.fullpath, self.seekable, self.is_video)]
        
        if self.is_video:
            items.append("    Dim(pixel):     {0} @ {1}\n"
                         "    Dim(display):   {2} @ {3}\n"
                         "    Subtitles:      {4}\n".format(
                    "{0[0]}x{0[1]}".format(self.original_dimension), self.original_dimension[2],
                    "{0[0]}x{0[1]}".format(self.scaled_dimension), self.scaled_dimension[2],
                    self.subtitle_had))
        logging.debug(''.join(items))

    def __gen_meta_info(self,info):
        self.filename = info["ID_FILENAME"]
        
        self.fullpath = os.path.realpath(self.filename)

        self.seekable = (info["ID_SEEKABLE"] == "1")
        self.is_video = True if "ID_VIDEO_ID" in info else False
        
    def __gen_video_info(self,info):
        self.original_dimension[0] = int(info["ID_VIDEO_WIDTH"])
        self.original_dimension[1] = int(info["ID_VIDEO_HEIGHT"])
        if "ID_VIDEO_ASPECT" in info:
            self.original_dimension[2] = Fraction(info["ID_VIDEO_ASPECT"]).limit_denominator(10)
        self.scaled_dimension = self.original_dimension[:]

        # amend the dim params
        if self.scaled_dimension[2] == 0:
            self.scaled_dimension[2] =  Fraction(self.scaled_dimension[0],self.scaled_dimension[1])

        # fix video width for non-square pixel, i.e. w/h != aspect
        # or the video expanding will not work
        if abs(Fraction(self.scaled_dimension[0],self.scaled_dimension[1]) - self.scaled_dimension[2]) > 0.1:
            self.scaled_dimension[0] = int(round(self.scaled_dimension[1] * self.scaled_dimension[2]))

    def __gen_subtitle_info(self,info):
        if "ID_SUBTITLE_ID" in info:
            self.subtitle_had = "embedded text"
        if "ID_FILE_SUB_ID" in info:
            self.subtitle_had = "external text"
        if "ID_VOBSUB_ID" in info:
            self.subtitle_had = "external vobsub"

@singleton
class IPCPipe(object):
    def send(self, cmd):
        self.writer.send(cmd)
    
    def terminate(self):
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

        self.reader, self.writer = multiprocessing.Pipe(False)

        self.__proc_listener = multiprocessing.Process(target=listen, args=(Fifo().path,))
        self.__proc_listener.start()

@singleton
class Fifo:
    def __init__(self):
        import tempfile
        self.__tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.__tmpdir, "mplayer_fifo")
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

    if sys.hexversion < 0x02060000:
        logging.info("Please run the script with python>=2.6.0")
    else:
        run()
