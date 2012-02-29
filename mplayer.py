#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010,2011 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <subi 2012/02/29 15:03:52>
#
# mplayer-wrapper is a simple frontend for MPlayer written in Python, trying to
# be a transparent interface. It is convenient to rename the script to "mplayer"
# and place it in your $PATH (don't overwrite the real MPlayer); you would not
# even notice its existence.

# TODO:
# 1. resume last played position
# 2. remember last volume
# 3. remember last volume/hue/contrast for continuous playing (don't need data persistance)
# 4. shooter sometimes return a false subtitle with the same time length. find a
#    cure. (using zenity, pygtk, or both?)
# 5. chardet instead of enca?
# 6. support a,b,c... in continuous playing?

import logging
import os
import sys
import struct
import threading
import urllib2
from subprocess import Popen
from subprocess import PIPE
from fractions import Fraction

def which(cmd):
    """Mimic the shell command "which".
    """
    def exefy(fullpath):
        return fullpath if os.access(fullpath, os.X_OK) else None

    pdir, exe = os.path.split(cmd)
    if pdir:
        return exefy(cmd)
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            fullpath = exefy(os.path.join(path,cmd))
            if fullpath: return fullpath
    return None
        
def check_dimension():
    """Select the maximal available screen dimension by xrandr.
    """
    dim = [640, 480]
    if which("xrandr"):
        p = Popen(["xrandr"], stdout = PIPE)
        for line in p.communicate()[0].splitlines():
            if '*' in line:
                d = line.split()[0].split('x')
                if d[0] > dim[0]: dim = map(int,d)

        dim.append(Fraction(dim[0],dim[1]))
    return dim

def expand_video(m, method = "ass", display_aspect = Fraction(4,3)):
    """Given a video metainfo "m", expand the video to the specified "display_aspect"
    with "method".

    Return the arguments list for mplayer.

    Video expanding does the job to attach two black bands to the top and bottom of
    the video. mplayer will render osds (subtitles etc.) within the bands.
        
    Two ways exist:
    1. -vf expand:
       everything done by mplayer, not compatible with libass (subtitle overlapping
       problem). Have to use the old plain subtitle renderer (-noass).
    2. -ass-use-margin:
       everything done by YOU, including the calculation of the margin heights and
       the font scales. The benefit is you can use "-ass".
        
    The "ass-use-margin" method leads a very annoying problem: the subtitle charecters
    are horizontally stretched. UGLY and UNACCEPTABLE. We need a fix.
        
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
    2. make font be of correct aspect (done by calculating a proper ScaleX)
        
    Addtionally, we also want to place the subtitle as close to the picture as
    possible (instead of the bottom of the screen, which is visual distracting).
    This can be done via the ASS style tag "MarginV", which is the relative relative
    in the ass rendering screen (i.e. the screan of PlayResX:PlayResY).
        
    Another approach is just adding a black band that is wide enough to contain
    the subtitles, avoiding the use of "MarginV".
    """
    # -subfont-autoscale has nothing to do with libass, only affect the osd and
    # the plain old subtitle renderer
    args = "-subfont-autoscale 2".split()

    if m.scaled_dimension[2] < Fraction(4,3):
        # assume we will never face a narrower screen than 4:3
        args.extend("-vf-pre dsize=4/3".split())
    elif method == "ass":
        # feel free to change the scale for your favor.
        ass_font_scale = 2
        args.extend("-ass -ass-font-scale {0}".format(ass_font_scale).split());

        subtitle_height_in_video = int(18*1.25/288 * ass_font_scale * m.scaled_dimension[1])
        target_aspect = Fraction(m.scaled_dimension[0], m.scaled_dimension[1]+subtitle_height_in_video*2)
        if target_aspect < display_aspect:
            target_aspect = display_aspect

        # expand_video_y:video_Y = (video_X/video_Y):(video_X/expanded_video_Y)
        m2t = m.scaled_dimension[2] / target_aspect
        
        if m2t > 1:
            margin = (m2t - 1) * m.scaled_dimension[1] / 2
            args.extend("-ass-use-margins -ass-bottom-margin {0} -ass-top-margin {0}".format(int(margin)).split())
            args.extend("-ass-force-style ScaleX={0}".format(1/float(m2t)).split())
    else:
        # -vf expand does its own non-square pixel adjustment;
        # m.original_dimension is fine
        args.extend("-subpos 98 -vf-pre expand={0}::::1:{1}".format(m.original_dimension[0],disp_aspect).split())
    return args

def genlist(path):
    """For the given path, generate a file list for continuous playing.
    """
    import locale,re
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

    if not os.path.exists(path):
        return [path]
    
    pdir, basename = os.path.split(os.path.abspath(path))

    # basic candidate filtering
    # 1. extention
    files = [f for f in os.listdir(pdir) if f.endswith(os.path.splitext(basename)[1])]
    # 2. sort and remove alphabetical header
    files.sort(key=make_sort_key)
    del files[0:files.index(basename)]

    # only one file in the candidate list
    if len(files) == 1:
        return [os.path.join(pdir,basename)]

    # find the common prefix
    keys = [split_by_int(f) for f in files[0:2]]
    prefix_items = []
    for key in zip(keys[0],keys[1]):
        if key[0] == key[1]: prefix_items.append(key[0])
        else: break
    prefix = ''.join(prefix_items)

    # generate the list
    results = [os.path.join(pdir,files[0])]
    for i,f in enumerate(files[1:]):
        if strip_to_int(f,prefix) - strip_to_int(files[i],prefix) == 1:
            results.append(os.path.join(pdir,f))
        else: break
    return results

class SubFetcher:
    """Reference: http://code.google.com/p/sevenever/source/browse/trunk/misc/fetchsub.py
    """
    subtitles = []
    save_dir = ""

    def do(self):
        self.save_dir = "test for mp"
        self.fetch()
        self.save()
        self.activate_in_mplayer()
        
    def __init__(self, m):
        self.save_dir = os.path.splitext(m.fullpath)[0]
        self.__build_data(m)
        
        self.req = urllib2.Request(self.url)
        for h in self.header: self.req.add_header(h[0],h[1])
        self.req.add_data(self.data)

    def fetch(self):
        def parse_package(response):
            c = response.read(8)
            package_length, desc_length = struct.unpack("!II", c)
            description = response.read(desc_length).decode("UTF-8")

            logging.info("Length of current package in bytes: {0}".format(package_length))
            
            c = response.read(5)
            package_length, file_count = struct.unpack("!IB", c)

            logging.info("{0} subtitles in current package ({1})".format(file_count,description))

            for j in range(file_count):
                parse_file(response)

        def parse_file(response):
            c = response.read(8)
            filepack_length, ext_length = struct.unpack("!II", c)

            file_ext = response.read(ext_length)
                
            c = response.read(4)
            file_length = struct.unpack("!I", c)[0]
            subtitle = response.read(file_length)
            if subtitle.startswith("\x1f\x8b"):
                import gzip
                from cStringIO import StringIO
                self.subtitles.append([file_ext, gzip.GzipFile(fileobj=StringIO(subtitle)).read()])
            else:
                logging.warning("Unknown format or incomplete data. Trying again...")

        response = urllib2.urlopen(self.req)

        c = response.read(1)
        package_count = struct.unpack("!b", c)[0]

        logging.info("{0} subtitle packages found".format(package_count))

        for i in range(package_count):
            parse_package(response)
        logging.info("{0} subtitle(s) fetched.".format(len(self.subtitles)))

        # todo: enumerate?
        for i in range(len(self.subtitles)):
            suffix = str(i) if i>0 else ""
            self.subtitles[i][0] = self.save_dir + suffix + '.' + self.subtitles[i][0]

    def save(self):
        enca = which("enca")
        for sub in self.subtitles:
            logging.info("Saving subtitle as {0}".format(sub[0]))
            f = open(sub[0],"wb")
            f.write(sub[1])
            f.close()
            if enca:
                logging.info("Convert {0} to UTF-8".format(sub[0]))
                Popen("enca -c -x utf8 -L zh".split()+[sub[0]]).communicate()

    def activate_in_mplayer(self):
        for sub in self.subtitles:
            MPlayer.cmd("sub_load \"{0}\"".format(sub[0]))
        MPlayer.cmd("sub_file 0")

    def __build_data(self,m):
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

        schemas = ["http", "https"]
        # [www, svlayer, splayer5].shooter.cn has issues
        servers = ["splayer1", "splayer2", "splayer3", "splayer4"]

        import random
        boundary = "-"*28 + "{0:x}".format(random.getrandbits(48))

        self.url = "{0}://{1}.shooter.cn/api/subapi.php".format(random.choice(schemas), random.choice(servers))
        
        self.header = []
        self.header.append(["User-Agent", "SPlayer Build ${0}".format(random.randint(1217,1543))])
        self.header.append(["Content-Type", "multipart/form-data; boundary={0}".format(boundary)])

        data = []
        data.append(["pathinfo", os.path.join("c:/", os.path.basename(os.path.dirname(m.fullpath)), os.path.basename(m.fullpath))])
        data.append(["filehash", hashing(m.fullpath)])
#        data.append(["lang", "chn"])

        self.data = ''.join(["--{0}\n"
                             "Content-Disposition: form-data; name=\"{1}\"\n"
                             "{2}\n".format(boundary, d[0], d[1]) for d in data]
                            + ["--" + boundary + "--"])

        logging.debug("Will query server {0} with\n"
                      "{1}\n"
                      "{2}\n".format(self.url,self.header,self.data))
        
class MPlayer(object):
    # TODO: "not compiled in option"
    last_timestamp = 0.0
    last_exit_status = None

    def identify(self,filelist=[]):
        p = Popen([self.__path]+"-vo null -ao null -frames 0 -identify".split()+filelist, stdout=PIPE, stderr=PIPE)
        return [l for l in p.communicate()[0].splitlines() if l.startswith("ID_")]

    def support(self,opt):
        # 1: take no param
        # 2: take 1 param
        if not self.__opts: self.__gen_opts()
        return self.__opts[opt] if opt in self.__opts else 0

    def cmd(self,cmd):
        if self.__process.poll() == None:
            logging.debug("Sending command <{0}> to <{1}>".format(cmd, self.__fifo_path))
            fifo = open(self.__fifo_path,"w")
            fifo.write(cmd+'\n')
            fifo.close()
        
    def play(self,args=[]):
        def tee(p, f=sys.stdout):
            def flush(f,lines):
                f.write(''.join(lines.pop(0)))
                f.flush()
                lines.append([])

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

        args = [self.__path] + self.__args + args

        logging.debug("Final command:\n{0}".format(' '.join(args)))

        if dry_run: return

        self.__process = Popen(args, stdin=sys.stdin, stdout=PIPE, stderr=None)
        tee(self.__process)

        logging.debug("Last timestamp: {0}".format(self.last_timestamp))
        logging.debug("Last exit status: {0}".format(self.last_exit_status))

    def __init__(self, fifo, default_args=[]):
        self.__path = None
        self.__fifo_path = fifo.path
        self.__args = fifo.args + default_args
        self.__opts = {}
        for p in ["/opt/bin/mplayer","/usr/local/bin/mplayer","/usr/bin/mplayer"]:
            if os.path.isfile(p): self.__path = p
        if not self.__path:
            raise RuntimeError,"Cannot find a mplayer binary."

    def __gen_opts(self):
        options = Popen([self.__path, "-list-options"], stdout=PIPE).communicate()[0].splitlines()
        options = options[3:len(options)-3]

        for line in options:
            s = line.split();
            opt = s[0].split(":") # take care of option:suboption
            if opt[0] in self.__opts: continue
            self.__opts[opt[0]] = (2 if len(opt)==2 or s[1]!="Flag" else 1)

        # handle vf* af*: mplayer reports option name as vf*, while it
        # is a family of options.
        del self.__opts['af*']
        del self.__opts['vf*']
        for extra in ["af","af-adv","af-add","af-pre","af-del","vf","vf-add","vf-pre","vf-del"]:
            self.__opts[extra] = 2
        for extra in ["af-clr","vf-clr"]:
            self.__opts[extra] = 1

class Media:
    """Construct media metadata by midentify; may apply "proper" fixes
    """
    exist = True

    filename = ""
    fullpath = ""
    
    seekable = True
    is_video = False

    original_dimension = [0,0,Fraction(0)]
    scaled_dimension = [0,0,Fraction(0)]

    subtitle_had = "none"

    def __init__(self, path, mplayer):
        """Parse the output by midentify.
        """
        info = {}
        for l in mplayer.identify([path]):
            a = l.split('=')
            info[a[0]] = a[1]

        if not "ID_FILENAME" in info:
            self.exist = False;
            return

        self.__gen_meta_info(info)
        
        if self.is_video:
            self.__gen_video_info(info)
            self.__gen_subtitle_info(info)
            
        self.__log()

    def __log(self):
        items = ["{0}\n"
                 "  Fullpath:               {1}\n"
                 "  Seekable:               {2}\n"
                 "  Video:                  {3}\n".format(self.filename, self.fullpath, self.seekable, self.is_video)]
        
        if self.is_video:
            items.append("    Dimension(pixel):     {0}\n"
                         "    Dimension(display):   {1}\n"
                         "    Aspect(display):      {2}\n"
                         "    Subtitles:            {3}\n".format(
                    "{0[0]}x{0[1]}".format(self.original_dimension),
                    "{0[0]}x{0[1]}".format(self.scaled_dimension),
                    float(self.scaled_dimension[2]),
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
            self.original_dimension[2] = Fraction(info["ID_VIDEO_ASPECT"])
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

class CmdLineParser:
    """ Attributes:
      self.application
      self.files
      self.args
      self.bad_args
    """
    def __init__(self, args, mplayer):
        self.__args = args[:]
        self.files = []
        self.args = []
        self.bad_args =[]

        a = os.path.basename(self.__args.pop(0))
        if "mplayer" in a:
            self.application = "player"
        elif "midentify" in a:
            self.application = "identifier"
            self.args = self.__args
            self.__args = []
        else:
            self.application = "unknown"

        while len(self.__args)>0:
            s = self.__args.pop(0)
            if s == "-debug":
                logging.root.setLevel(logging.DEBUG)
            elif s == "-dry-run":
                logging.root.setLevel(logging.DEBUG)
                global dry_run
                dry_run = True
            elif s == "--":
                self.files.extend(self.__args)
                self.__args = []
            elif s.startswith("-"):
                flag = mplayer.support(s.split('-',1)[1])
                if flag == 0:
                    self.bad_args.append(s)
                elif flag == 1:
                    self.args.append(s)
                elif flag == 2:
                    self.args.append(s)
                    if len(self.__args)>0:
                        self.args.append(self.__args.pop(0))
            else:
                self.files.append(s)

# http://www.python.org/dev/peps/pep-0318/
def singleton(cls):
    instances = {}
    def getinstance():
        if cls not in instances:
            instances[cls] = cls()
        return instances[cls]
    return getinstance

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
    fifo = Fifo()
    mplayer = MPlayer(fifo)
    parser = CmdLineParser(sys.argv,mplayer)

    if parser.application == "identifier":
        print '\n'.join(mplayer.identify(parser.args))
        return

    if parser.application == "player":
        screen_dim = check_dimension()

        playlist = parser.files
        if len(parser.files) == 1:
            playlist = genlist(parser.files[0])

        if len(playlist) == 0:
            mplayer.play(parser.args)
        for f in playlist:
            media = Media(f, mplayer)
            args = parser.args

            if not media.exist:
                logging.debug("{0} does not exist".format(f))

            if media.is_video:
                # todo: what if -noass specified?
                args += expand_video(media, "ass", screen_dim[2])

            SubFetcher(media)
            mplayer.play(args+[f])

            if mplayer.last_exit_status == "Quit":
                break

if __name__ == "__main__":
    dry_run = False
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)

    run()
